#!/usr/bin/env python3
"""
gl_poll_daemon.py — GitLab Issue Polling Daemon

GitLab リポジトリを定期ポーリングし、新規 status:open イシューを発見したら
claude CLI を起動してワーカーワークフローを実行する。

LLM によって発動されることはなく、OS の常駐サービスとして動作する。

Config:
  Linux/macOS : ~/.config/gitlab-idd/config.json
  Windows     : %APPDATA%\\gitlab-idd\\config.json

Usage:
  python gl_poll_daemon.py              # 常駐ループ起動（blocks）
  python gl_poll_daemon.py --once       # 1 回だけポーリングして終了
  python gl_poll_daemon.py --interval N # インターバル上書き（秒）

Environment:
  GITLAB_TOKEN または GL_TOKEN  トークンが config に未設定の場合に使用
"""

from __future__ import annotations

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
from pathlib import Path


# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------

DEFAULT_POLL_INTERVAL = 300  # 5 分


def get_config_dir() -> Path:
    """OS に応じた設定ディレクトリを返す。"""
    system = platform.system()
    if system == "Windows":
        base = Path(os.environ.get("APPDATA", Path.home() / "AppData" / "Roaming"))
    elif system == "Darwin":
        base = Path.home() / "Library" / "Application Support"
    else:  # Linux / other POSIX
        base = Path(os.environ.get("XDG_CONFIG_HOME", Path.home() / ".config"))
    return base / "gitlab-idd"


def get_config_path() -> Path:
    return get_config_dir() / "config.json"


def get_log_path() -> Path:
    return get_config_dir() / "daemon.log"


def load_config() -> dict:
    path = get_config_path()
    if not path.exists():
        return {
            "poll_interval_seconds": DEFAULT_POLL_INTERVAL,
            "repos": [],
            "seen_issues": {},
        }
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def save_config(config: dict) -> None:
    path = get_config_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".json.tmp")
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(config, f, ensure_ascii=False, indent=2)
    tmp.replace(path)
    # パーミッションを所有者のみ読み書き可能に設定（Unix 系）
    if platform.system() != "Windows":
        try:
            os.chmod(path, 0o600)
        except OSError:
            pass


# ---------------------------------------------------------------------------
# GitLab API
# ---------------------------------------------------------------------------


def gitlab_get(host: str, token: str, path: str, params: dict = None) -> object:
    """GitLab REST API に GET リクエストを行い JSON を返す。失敗時は None。"""
    url = f"https://{host}/api/v4{path}"
    if params:
        filtered = {k: v for k, v in params.items() if v is not None}
        url += "?" + urllib.parse.urlencode(filtered)
    req = urllib.request.Request(url, headers={"PRIVATE-TOKEN": token, "Accept": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            return json.loads(resp.read())
    except urllib.error.HTTPError as e:
        logging.warning("GitLab API HTTP %s for %s", e.code, path)
        return None
    except Exception as e:
        logging.warning("Network error polling %s: %s", path, e)
        return None


def fetch_open_issues(host: str, token: str, project: str) -> list:
    """status:open かつ assignee:any のイシューを取得する。"""
    ep = urllib.parse.quote(project, safe="")
    result = gitlab_get(host, token, f"/projects/{ep}/issues", params={
        "state": "opened",
        "labels": "status:open,assignee:any",
        "per_page": 100,
    })
    return result if isinstance(result, list) else []


# ---------------------------------------------------------------------------
# claude CLI 呼び出し
# ---------------------------------------------------------------------------


def find_claude() -> str:
    """claude コマンドのパスを返す。見つからなければ空文字。"""
    return shutil.which("claude") or ""


def launch_claude_worker(issue: dict, repo: dict) -> None:
    """新規イシューを処理するために claude -p を非同期起動する。"""
    claude = find_claude()
    if not claude:
        logging.warning("claude コマンドが見つかりません。イシュー #%s をスキップ", issue.get("iid"))
        return

    local_path = repo.get("local_path") or "."
    issue_id = issue.get("iid", "?")
    issue_title = issue.get("title", "")
    issue_url = issue.get("web_url", "")

    prompt = (
        "gitlab-idd ワーカーとして、以下の GitLab イシューを拾って実行してください。\n"
        f"イシュー ID: {issue_id}\n"
        f"タイトル: {issue_title}\n"
        f"URL: {issue_url}\n\n"
        "SKILL.md の「ワーカー — イシュー取得・実行・報告」フローに従い、"
        "イシューを担当→実装→ブランチ push →コメント報告まで一気通貫で実行してください。"
    )

    log_file = get_config_dir() / f"worker-issue-{issue_id}.log"
    logging.info("claude を起動: イシュー #%s in %s", issue_id, local_path)

    try:
        kwargs: dict = {
            "cwd": local_path,
            "stdout": open(log_file, "w", encoding="utf-8"),
            "stderr": subprocess.STDOUT,
            "start_new_session": True,  # 親プロセス終了時に巻き込まれない
        }
        # Windows では start_new_session の代わりに DETACHED_PROCESS を使う
        if platform.system() == "Windows":
            kwargs.pop("start_new_session")
            kwargs["creationflags"] = 0x00000008  # DETACHED_PROCESS
        subprocess.Popen([claude, "-p", prompt], **kwargs)
    except Exception as e:
        logging.error("claude 起動失敗 (イシュー #%s): %s", issue_id, e)


# ---------------------------------------------------------------------------
# デスクトップ通知（best-effort）
# ---------------------------------------------------------------------------


def send_notification(title: str, message: str) -> None:
    """デスクトップ通知を送る。失敗してもエラーにしない。"""
    system = platform.system()
    try:
        if system == "Darwin":
            safe_title = title.replace('"', '\\"')
            safe_msg = message.replace('"', '\\"')
            subprocess.run(
                ["osascript", "-e", f'display notification "{safe_msg}" with title "{safe_title}"'],
                check=False, capture_output=True,
            )
        elif system == "Linux":
            subprocess.run(["notify-send", title, message], check=False, capture_output=True)
        elif system == "Windows":
            # PowerShell の BalloonTip を使ってトースト通知
            ps_script = (
                'Add-Type -AssemblyName System.Windows.Forms;'
                '$n = New-Object System.Windows.Forms.NotifyIcon;'
                '$n.Icon = [System.Drawing.SystemIcons]::Information;'
                '$n.Visible = $true;'
                f'$n.ShowBalloonTip(5000, "{title}", "{message}", [System.Windows.Forms.ToolTipIcon]::Info);'
                'Start-Sleep -Milliseconds 5500; $n.Dispose()'
            )
            subprocess.Popen(
                ["powershell", "-NoProfile", "-WindowStyle", "Hidden", "-Command", ps_script],
                creationflags=0x08000000,  # CREATE_NO_WINDOW
            )
    except Exception:
        pass


# ---------------------------------------------------------------------------
# ポーリングロジック
# ---------------------------------------------------------------------------


def repo_key(repo: dict) -> str:
    return f"{repo['host']}|{repo['project']}"


def get_token_for_repo(repo: dict) -> str:
    """リポジトリ用トークンを取得する。config → 環境変数の順で検索。"""
    return (
        repo.get("token")
        or os.environ.get("GITLAB_TOKEN")
        or os.environ.get("GL_TOKEN")
        or ""
    )


def poll_repo(repo: dict, config: dict) -> list:
    """1 リポジトリをポーリングし、新規イシューのリストを返す。"""
    host = repo.get("host", "")
    project = repo.get("project", "")
    token = get_token_for_repo(repo)

    if not token:
        logging.warning("トークン未設定: %s/%s — スキップ", host, project)
        return []

    issues = fetch_open_issues(host, token, project)
    key = repo_key(repo)
    seen = set(config.get("seen_issues", {}).get(key, []))
    return [i for i in issues if i.get("iid") not in seen]


def mark_seen(config: dict, repo: dict, issues: list) -> None:
    """イシューを既読（seen）としてマークする。"""
    key = repo_key(repo)
    seen = set(config.setdefault("seen_issues", {}).get(key, []))
    for issue in issues:
        seen.add(issue["iid"])
    config["seen_issues"][key] = sorted(seen)


def run_poll_cycle(config: dict) -> None:
    """全リポジトリに対して 1 サイクルのポーリングを実行する。"""
    repos = config.get("repos", [])
    if not repos:
        logging.debug("ポーリング対象リポジトリなし")
        return

    for repo in repos:
        host = repo.get("host", "?")
        project = repo.get("project", "?")
        logging.debug("ポーリング中: %s/%s", host, project)

        try:
            new_issues = poll_repo(repo, config)
        except Exception as e:
            logging.error("ポーリングエラー %s/%s: %s", host, project, e)
            continue

        if new_issues:
            logging.info("%d 件の新規イシュー: %s/%s", len(new_issues), host, project)
            for issue in new_issues:
                iid = issue.get("iid", "?")
                title = issue.get("title", "")
                logging.info("  イシュー #%s: %s", iid, title)
                send_notification(
                    f"GitLab 新規イシュー ({host}/{project})",
                    f"#{iid} {title}",
                )
                launch_claude_worker(issue, repo)
            mark_seen(config, repo, new_issues)
            save_config(config)


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
        description="GitLab issue polling daemon for gitlab-idd skill",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    parser.add_argument("--once", action="store_true", help="1 回ポーリングして終了")
    parser.add_argument("--interval", type=int, metavar="SECS", help="インターバル上書き（秒）")
    args = parser.parse_args()

    setup_logging()
    logging.info("gitlab-idd poll daemon 起動 (pid=%s, python=%s)", os.getpid(), sys.executable)

    config = load_config()
    if args.interval:
        config["poll_interval_seconds"] = args.interval

    if args.once:
        logging.info("--once モード: 1 回ポーリングして終了")
        run_poll_cycle(config)
        return

    interval = config.get("poll_interval_seconds", DEFAULT_POLL_INTERVAL)
    logging.info("インターバル: %s 秒 / 設定: %s", interval, get_config_path())

    while True:
        try:
            # 毎サイクル設定を再読み込みして動的変更に対応
            config = load_config()
            interval = config.get("poll_interval_seconds", DEFAULT_POLL_INTERVAL)
            run_poll_cycle(config)
        except Exception as e:
            logging.error("予期しないエラー: %s", e)
        time.sleep(interval)


if __name__ == "__main__":
    main()
