#!/usr/bin/env python3
"""
gl_poll_daemon.py — GitLab Issue Polling Daemon

GitLab リポジトリを定期ポーリングし、新規 status:open イシューを発見したら
利用可能なエージェント CLI を起動してワーカーワークフローを実行する。

対応 CLI（優先順）: claude → codex → kiro → amazonq
  kiro は Windows 環境では WSL2 経由で実行する。

LLM によって発動されることはなく、OS バックグラウンドサービスとして動作する。
インストールは gl_poll_setup.py が担当する。

Requirements: Python 3.11+  /  stdlib only

Config:
  Linux   : ~/.config/gitlab-idd/config.json
  macOS   : ~/Library/Application Support/gitlab-idd/config.json
  Windows : %APPDATA%\\gitlab-idd\\config.json

Usage:
  python gl_poll_daemon.py              # 常駐ループ起動（blocks）
  python gl_poll_daemon.py --once       # 1 回だけポーリングして終了
  python gl_poll_daemon.py --dry-run    # モックデータで動作テスト（副作用なし）
  python gl_poll_daemon.py --interval N # インターバル上書き（秒）

Environment:
  GITLAB_TOKEN / GL_TOKEN  トークンが config に未設定の場合に使用
"""

import argparse
import json
import logging
import os
import platform
import shutil
import subprocess
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass, field
from pathlib import Path
from typing import NotRequired, TypedDict


# ---------------------------------------------------------------------------
# 型定義
# ---------------------------------------------------------------------------

class RepoConfig(TypedDict):
    host: str
    project: str
    local_path: str
    token: NotRequired[str]


class DaemonConfig(TypedDict):
    poll_interval_seconds: int
    repos: list[RepoConfig]
    seen_issues: dict[str, list[int]]
    preferred_cli: NotRequired[str]


DEFAULT_POLL_INTERVAL = 300  # 5 分


# ---------------------------------------------------------------------------
# エージェント CLI 検出
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class AgentCLI:
    """エージェント CLI の呼び出し情報。"""
    name: str           # "claude" / "codex" / "kiro" / "amazonq"
    binary: str         # 実行ファイルのフルパスまたは名前
    prompt_args: list[str] = field(default_factory=list)  # プロンプト前のオプション
    via_wsl: bool = False  # Windows + kiro 専用

    def build_command(self, prompt: str, local_path: str | None = None) -> list[str]:
        """CLI を起動するコマンドリストを生成する。"""
        match self.via_wsl:
            case True:
                # kiro on Windows: wsl kiro-cli agent [--cwd /mnt/c/...] "prompt"
                cmd = ["wsl", self.binary] + self.prompt_args
                if local_path:
                    cmd += ["--cwd", _win_to_wsl_path(local_path)]
                cmd.append(prompt)
            case False:
                cmd = [self.binary] + self.prompt_args + [prompt]
        return cmd


def _win_to_wsl_path(win_path: str) -> str:
    """Windows パスを WSL マウントパスに変換する。例: C:\\foo → /mnt/c/foo"""
    p = Path(win_path)
    drive = p.drive.rstrip(":").lower()  # "C:" → "c"
    rest = p.as_posix()[len(p.drive):]  # "/foo/bar"
    return f"/mnt/{drive}{rest}"


def _check_wsl_kiro() -> bool:
    """WSL2 内に kiro-cli が存在するか確認する。"""
    try:
        r = subprocess.run(
            ["wsl", "kiro-cli", "--version"],
            capture_output=True, timeout=10,
        )
        return r.returncode == 0
    except Exception:
        return False


def _verify_cli(binary: str) -> bool:
    """バイナリが動作することを確認する。"""
    try:
        r = subprocess.run([binary, "--version"], capture_output=True, timeout=10)
        return r.returncode == 0
    except Exception:
        return False


# CLI 候補: (name, binary_name, prompt_args)
_CLI_CANDIDATES: list[tuple[str, str, list[str]]] = [
    ("claude",   "claude",    ["-p"]),           # Claude Code (non-interactive print)
    ("codex",    "codex",     ["-q"]),            # OpenAI Codex CLI (quiet)
    ("kiro",     "kiro-cli",  ["agent"]),         # Kiro (native, non-Windows)
    ("amazonq",  "q",         ["chat"]),          # Amazon Q Developer CLI
]


def find_available_agent_cli(preferred: str | None = None) -> AgentCLI | None:
    """
    利用可能なエージェント CLI を自動検出して返す。
    preferred が指定された場合はそれを優先する。
    対応: claude → codex → kiro → amazonq
    Windows + kiro の場合は WSL2 経由を確認する。
    """
    system = platform.system()
    found: list[AgentCLI] = []

    for name, binary_name, prompt_args in _CLI_CANDIDATES:
        if system == "Windows" and name == "kiro":
            if _check_wsl_kiro():
                found.append(AgentCLI(name, binary_name, prompt_args, via_wsl=True))
        else:
            if path := shutil.which(binary_name):
                if _verify_cli(path):
                    found.append(AgentCLI(name, path, prompt_args))

    if not found:
        return None

    if preferred:
        for cli in found:
            if cli.name == preferred:
                return cli

    return found[0]


# ---------------------------------------------------------------------------
# 設定ディレクトリ・ファイル管理
# ---------------------------------------------------------------------------

def get_config_dir() -> Path:
    """OS に応じた設定ディレクトリを返す。"""
    match platform.system():
        case "Windows":
            base = Path(os.environ.get("APPDATA", Path.home() / "AppData" / "Roaming"))
        case "Darwin":
            base = Path.home() / "Library" / "Application Support"
        case _:
            base = Path(os.environ.get("XDG_CONFIG_HOME", Path.home() / ".config"))
    return base / "gitlab-idd"


def get_config_path() -> Path:
    return get_config_dir() / "config.json"


def get_log_path() -> Path:
    return get_config_dir() / "daemon.log"


def load_config() -> DaemonConfig:
    path = get_config_path()
    if not path.exists():
        return DaemonConfig(
            poll_interval_seconds=DEFAULT_POLL_INTERVAL,
            repos=[],
            seen_issues={},
        )
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def save_config(config: DaemonConfig) -> None:
    path = get_config_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".json.tmp")
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(config, f, ensure_ascii=False, indent=2)
    tmp.replace(path)
    if platform.system() != "Windows":
        try:
            os.chmod(path, 0o600)
        except OSError:
            pass


# ---------------------------------------------------------------------------
# GitLab API
# ---------------------------------------------------------------------------

def gitlab_get(host: str, token: str, path: str, params: dict | None = None) -> object:
    """GitLab REST API に GET して JSON を返す。失敗時は None。"""
    url = f"https://{host}/api/v4{path}"
    if params:
        url += "?" + urllib.parse.urlencode({k: v for k, v in params.items() if v is not None})
    req = urllib.request.Request(
        url, headers={"PRIVATE-TOKEN": token, "Accept": "application/json"}
    )
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            return json.loads(resp.read())
    except urllib.error.HTTPError as e:
        logging.warning("GitLab API HTTP %s: %s", e.code, path)
        return None
    except Exception as e:
        logging.warning("Network error: %s", e)
        return None


def fetch_open_issues(host: str, token: str, project: str) -> list[dict]:
    """status:open + assignee:any のイシューを取得する。"""
    ep = urllib.parse.quote(project, safe="")
    result = gitlab_get(host, token, f"/projects/{ep}/issues", params={
        "state": "opened",
        "labels": "status:open,assignee:any",
        "per_page": 100,
    })
    return result if isinstance(result, list) else []


# ---------------------------------------------------------------------------
# ドライラン用モックデータ
# ---------------------------------------------------------------------------

def _mock_issues(repo: RepoConfig) -> list[dict]:
    """ドライラン時の偽イシューを返す。"""
    return [
        {
            "iid": 9999,
            "title": "[DRYRUN] モックイシュー — デーモン動作テスト",
            "web_url": f"https://{repo['host']}/{repo['project']}/-/issues/9999",
            "author": {"username": "dryrun-user"},
            "labels": ["status:open", "assignee:any"],
        }
    ]


# ---------------------------------------------------------------------------
# ワーカー起動
# ---------------------------------------------------------------------------

def _make_worker_prompt(issue: dict) -> str:
    return (
        "gitlab-idd ワーカーとして、以下の GitLab イシューを拾って実行してください。\n"
        f"イシュー ID: {issue.get('iid', '?')}\n"
        f"タイトル: {issue.get('title', '')}\n"
        f"URL: {issue.get('web_url', '')}\n\n"
        "SKILL.md の「ワーカー — イシュー取得・実行・報告」フローに従い、"
        "イシューを担当→実装→ブランチ push →コメント報告まで一気通貫で実行してください。"
    )


def launch_agent_worker(
    issue: dict, repo: RepoConfig, cli: AgentCLI, *, dry_run: bool = False
) -> None:
    """イシューを処理するためにエージェント CLI を非同期起動する。"""
    local_path = repo.get("local_path") or "."
    issue_id = issue.get("iid", "?")
    prompt = _make_worker_prompt(issue)
    cmd = cli.build_command(prompt, local_path if cli.via_wsl else None)

    if dry_run:
        logging.info("[DRYRUN] 起動予定コマンド (イシュー #%s):", issue_id)
        logging.info("  cwd : %s", local_path)
        logging.info("  cmd : %s", " ".join(cmd))
        return

    log_file = get_config_dir() / f"worker-issue-{issue_id}.log"
    logging.info("[%s] 起動: イシュー #%s in %s", cli.name, issue_id, local_path)

    try:
        cwd = local_path if not cli.via_wsl else None  # WSL 側は --cwd で渡す
        kwargs: dict = {
            "cwd": cwd,
            "stdout": open(log_file, "w", encoding="utf-8"),
            "stderr": subprocess.STDOUT,
        }
        match platform.system():
            case "Windows":
                kwargs["creationflags"] = 0x00000008  # DETACHED_PROCESS
            case _:
                kwargs["start_new_session"] = True
        subprocess.Popen(cmd, **kwargs)
    except Exception as e:
        logging.error("CLI 起動失敗 (イシュー #%s): %s", issue_id, e)


# ---------------------------------------------------------------------------
# デスクトップ通知（best-effort）
# ---------------------------------------------------------------------------

def send_notification(title: str, message: str) -> None:
    try:
        match platform.system():
            case "Darwin":
                safe = lambda s: s.replace('"', '\\"')
                subprocess.run(
                    ["osascript", "-e",
                     f'display notification "{safe(message)}" with title "{safe(title)}"'],
                    check=False, capture_output=True,
                )
            case "Linux":
                subprocess.run(["notify-send", title, message],
                               check=False, capture_output=True)
            case "Windows":
                ps = (
                    "Add-Type -AssemblyName System.Windows.Forms;"
                    "$n=New-Object System.Windows.Forms.NotifyIcon;"
                    "$n.Icon=[System.Drawing.SystemIcons]::Information;"
                    "$n.Visible=$true;"
                    f'$n.ShowBalloonTip(5000,"{title}","{message}",'
                    "[System.Windows.Forms.ToolTipIcon]::Info);"
                    "Start-Sleep -Milliseconds 5500;$n.Dispose()"
                )
                subprocess.Popen(
                    ["powershell", "-NoProfile", "-WindowStyle", "Hidden", "-Command", ps],
                    creationflags=0x08000000,
                )
    except Exception:
        pass


# ---------------------------------------------------------------------------
# ポーリングロジック
# ---------------------------------------------------------------------------

def repo_key(repo: RepoConfig) -> str:
    return f"{repo['host']}|{repo['project']}"


def get_token_for_repo(repo: RepoConfig) -> str:
    return (
        repo.get("token")
        or os.environ.get("GITLAB_TOKEN")
        or os.environ.get("GL_TOKEN")
        or ""
    )


def poll_repo(
    repo: RepoConfig, config: DaemonConfig, *, dry_run: bool = False
) -> list[dict]:
    """1 リポジトリをポーリングし、新規イシューを返す。"""
    if dry_run:
        all_issues = _mock_issues(repo)
    else:
        host = repo.get("host", "")
        project = repo.get("project", "")
        token = get_token_for_repo(repo)
        if not token:
            logging.warning("トークン未設定: %s/%s — スキップ", host, project)
            return []
        all_issues = fetch_open_issues(host, token, project)

    key = repo_key(repo)
    seen = set(config.get("seen_issues", {}).get(key, []))
    return [i for i in all_issues if i.get("iid") not in seen]


def mark_seen(config: DaemonConfig, repo: RepoConfig, issues: list[dict]) -> None:
    key = repo_key(repo)
    seen = set(config.setdefault("seen_issues", {}).get(key, []))
    for issue in issues:
        seen.add(issue["iid"])
    config["seen_issues"][key] = sorted(seen)


def run_poll_cycle(
    config: DaemonConfig, cli: AgentCLI, *, dry_run: bool = False
) -> None:
    """全リポジトリに対して 1 サイクルのポーリングを実行する。"""
    repos = config.get("repos", [])
    if not repos:
        logging.debug("ポーリング対象リポジトリなし")
        return

    tag = "[DRYRUN] " if dry_run else ""

    for repo in repos:
        host = repo.get("host", "?")
        project = repo.get("project", "?")
        logging.debug("%sポーリング中: %s/%s", tag, host, project)

        try:
            new_issues = poll_repo(repo, config, dry_run=dry_run)
        except Exception as e:
            logging.error("%sポーリングエラー %s/%s: %s", tag, host, project, e)
            continue

        if not new_issues:
            continue

        logging.info("%s%d 件の新規イシュー: %s/%s", tag, len(new_issues), host, project)
        for issue in new_issues:
            logging.info("  %sイシュー #%s: %s", tag, issue.get("iid", "?"), issue.get("title", ""))
            send_notification(
                f"{tag}GitLab 新規イシュー ({host}/{project})",
                f"#{issue.get('iid', '?')} {issue.get('title', '')}",
            )
            launch_agent_worker(issue, repo, cli, dry_run=dry_run)

        if not dry_run:
            mark_seen(config, repo, new_issues)
            save_config(config)
        else:
            logging.info("[DRYRUN] config.json は更新しません（seen_issues 変更なし）")


# ---------------------------------------------------------------------------
# メインループ
# ---------------------------------------------------------------------------

def setup_logging() -> None:
    log_path = get_log_path()
    log_path.parent.mkdir(parents=True, exist_ok=True)
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)-8s %(message)s",
        handlers=[
            logging.FileHandler(log_path, encoding="utf-8"),
            logging.StreamHandler(sys.stdout),
        ],
    )


def main() -> None:
    parser = argparse.ArgumentParser(
        description="GitLab issue polling daemon (Python 3.11+, stdlib only)",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    parser.add_argument("--once",     action="store_true", help="1 回ポーリングして終了")
    parser.add_argument("--dry-run",  action="store_true", help="モックデータで動作テスト（副作用なし）")
    parser.add_argument("--interval", type=int, metavar="SECS", help="インターバル上書き（秒）")
    args = parser.parse_args()

    setup_logging()

    if args.dry_run:
        logging.info("=" * 60)
        logging.info("DRYRUN モード: 副作用は一切発生しません")
        logging.info("  - GitLab API はモックデータを使用")
        logging.info("  - エージェント CLI は起動しません（コマンドを表示のみ）")
        logging.info("  - config.json は更新しません")
        logging.info("=" * 60)

    logging.info("gitlab-idd poll daemon 起動 (pid=%s, python=%s)", os.getpid(), sys.executable)

    # エージェント CLI 検出
    config = load_config()
    preferred = config.get("preferred_cli")
    cli = find_available_agent_cli(preferred)

    if cli is None:
        logging.error(
            "エージェント CLI が見つかりません。"
            "claude / codex / kiro-cli / q のいずれかをインストールしてください。"
        )
        if not args.dry_run:
            sys.exit(1)
        # dry-run では続行してモック動作を見せる
        cli = AgentCLI("mock", "echo", ["[DRYRUN]"])

    logging.info("使用 CLI: %s (%s)", cli.name, "WSL経由" if cli.via_wsl else cli.binary)

    if args.interval:
        config["poll_interval_seconds"] = args.interval

    if args.once or args.dry_run and not args.once:
        # --dry-run 単独の場合も --once 相当で実行
        run_poll_cycle(config, cli, dry_run=args.dry_run)
        if args.dry_run and not args.once:
            return

    if args.once:
        return

    interval = config.get("poll_interval_seconds", DEFAULT_POLL_INTERVAL)
    logging.info("常駐ループ開始: インターバル %s 秒 / 設定 %s", interval, get_config_path())

    while True:
        try:
            config = load_config()  # 毎サイクル再読み込み
            interval = config.get("poll_interval_seconds", DEFAULT_POLL_INTERVAL)
            cli = find_available_agent_cli(config.get("preferred_cli")) or cli
            run_poll_cycle(config, cli, dry_run=args.dry_run)
        except Exception as e:
            logging.error("予期しないエラー: %s", e)
        time.sleep(interval)


if __name__ == "__main__":
    main()
