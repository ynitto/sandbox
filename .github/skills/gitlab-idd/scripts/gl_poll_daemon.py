#!/usr/bin/env python3
"""
gl_poll_daemon.py — GitLab Issue Polling Daemon

GitLab リポジトリを定期ポーリングし、新規 status:open イシューを発見したら
利用可能なエージェント CLI を起動してワーカーワークフローを実行する。

対応 CLI（優先順）: claude → codex → kiro → amazonq
  kiro は Windows 環境では WSL2 経由で実行し、WSL 内でリポジトリをクローンする。

LLM によって発動されることはなく、OS バックグラウンドサービスとして動作する。
インストールは gl_poll_setup.py が担当する。

Requirements: Python 3.11+  /  stdlib only

Config:
  Linux   : ~/.config/gitlab-idd/config.json
  macOS   : ~/Library/Application Support/gitlab-idd/config.json
  Windows : %APPDATA%\\gitlab-idd\\config.json

Usage:
  python gl_poll_daemon.py              # 常駐ループ起動
  python gl_poll_daemon.py --once       # 1 回だけポーリングして終了
  python gl_poll_daemon.py --dry-run    # 実 GitLab API + モック CLI でテスト
  python gl_poll_daemon.py --interval N # インターバル上書き（秒）

  --dry-run の動作:
    - GitLab API は実際に呼び出す（実データで確認可能）
    - CLI は起動せず、プロンプトを mock-prompts/ に保存する
    - config.json の seen_issues は更新しない

Environment:
  GITLAB_TOKEN / GL_TOKEN  トークンが config に未設定の場合に使用
"""

import argparse
import json
import logging
import logging.handlers
import os
import platform
import subprocess
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime
from pathlib import Path
from string import Template

from gl_common import (
    RepoConfig, DaemonConfig, DEFAULT_POLL_INTERVAL,
    AgentCLI,
    find_available_agent_clis, find_best_agent_cli,
    get_config_dir, get_config_path,
    load_config, save_config,
    title_to_slug,
    retry_on_network_error,
)


# ---------------------------------------------------------------------------
# デーモン固有のパス管理
# ---------------------------------------------------------------------------

def get_log_path() -> Path:
    return get_config_dir() / "daemon.log"


# ---------------------------------------------------------------------------
# プロンプトテンプレート
# ---------------------------------------------------------------------------

# フォールバック用の最小テンプレート（ファイルが見つからない場合に使用）
_FALLBACK_PROMPT = """\
gitlab-idd ワーカーとして、以下の GitLab イシューを担当・実行してください。

イシュー ID: #${issue_id}
タイトル: ${issue_title}
URL: ${issue_url}
プロジェクト: ${host}/${project}
ローカルパス: ${local_path}
ブランチ名: ${branch_name}

## イシュー本文

${issue_body}

---

SKILL.md のワーカーフローに従い、assign → 実装 → push → MR → コメント報告を実行してください。
"""

_FALLBACK_WSL_KIRO_PROMPT = """\
このタスクは WSL2 環境で kiro が実行します。まず以下を実行してください:

  mkdir -p /tmp/gitlab-idd-work
  cd /tmp/gitlab-idd-work
  if [ $? -ne 0 ]; then exit $?; fi
  git clone https://${host}/${project}.git
  if [ $? -ne 0 ]; then exit $?; fi
  cd ${project_name}
  if [ $? -ne 0 ]; then exit $?; fi

イシュー ID: #${issue_id}
タイトル: ${issue_title}
URL: ${issue_url}
ブランチ名: ${branch_name}

## イシュー本文

${issue_body}

---

SKILL.md のワーカーフローに従い、WSL 内でリポジトリをクローンした上で実装・push・報告を実行してください。
"""


def _find_template_dir() -> Path | None:
    """テンプレートディレクトリを探す（設定ディレクトリ優先 → スキルリポジトリ）。"""
    # 1. インストール済みの場所
    installed = get_config_dir() / "templates"
    if installed.is_dir():
        return installed
    # 2. スキルリポジトリ内 (.../gitlab-idd/scripts/../templates)
    repo_templates = Path(__file__).parent.parent / "templates"
    if repo_templates.is_dir():
        return repo_templates
    return None


def load_template(name: str) -> str | None:
    """テンプレートファイルを読み込む。見つからない場合は None を返す。"""
    tdir = _find_template_dir()
    if tdir:
        path = tdir / name
        if path.exists():
            return path.read_text(encoding="utf-8")
    logging.debug("テンプレート未発見: %s（フォールバック使用）", name)
    return None


def build_worker_prompt(issue: dict, repo: RepoConfig, *, via_wsl_kiro: bool = False) -> str:
    """
    イシューデータをテンプレートに埋め込み、ワーカー向けプロンプトを生成する。
    LLM による動的生成は行わず、Python の string.Template で置換する。
    """
    template_name = "worker-prompt-wsl-kiro.md" if via_wsl_kiro else "worker-prompt.md"
    template_str = load_template(template_name)
    if template_str is None:
        template_str = _FALLBACK_WSL_KIRO_PROMPT if via_wsl_kiro else _FALLBACK_PROMPT

    issue_id = issue.get("iid", "unknown")
    project_name = repo["project"].split("/")[-1]

    _title = issue.get("title", "")
    _slug = title_to_slug(_title)

    variables = {
        "issue_id":     str(issue_id),
        "issue_title":  _title,
        "issue_url":    issue.get("web_url", ""),
        "issue_body":   (issue.get("description") or "（本文なし）").strip(),
        "issue_labels": ", ".join(issue.get("labels") or []),
        "host":         repo["host"],
        "project":      repo["project"],
        "project_name": project_name,
        "local_path":   repo.get("local_path") or ".",
        "branch_name":  f"feature/issue-{issue_id}-{_slug}",
        "remote_url":   f"https://{repo['host']}/{repo['project']}.git",
        "clone_dir":    f"/tmp/gitlab-idd-work/{project_name}",
    }

    # string.Template の safe_substitute: 未定義変数は そのまま残す
    return Template(template_str).safe_substitute(variables)


# ---------------------------------------------------------------------------
# GitLab API
# ---------------------------------------------------------------------------

def _http_get(url: str, token: str) -> tuple[bytes, str]:
    """1 回分の HTTP GET を実行し (body, next_page_header) を返す。"""
    req = urllib.request.Request(
        url, headers={"PRIVATE-TOKEN": token, "Accept": "application/json"}
    )
    with urllib.request.urlopen(req, timeout=30) as resp:
        return resp.read(), resp.headers.get("X-Next-Page", "").strip()


def gitlab_get(host: str, token: str, path: str, params: dict | None = None) -> object:
    """GitLab REST API に GET して JSON を返す。一時的なネットワーク障害はリトライ。失敗時は None。"""
    url = f"https://{host}/api/v4{path}"
    if params:
        url += "?" + urllib.parse.urlencode({k: v for k, v in params.items() if v is not None})
    try:
        body, _ = retry_on_network_error(_http_get, url, token, logger=logging.getLogger(__name__))
        return json.loads(body)
    except urllib.error.HTTPError as e:
        logging.warning("GitLab API HTTP %s: %s", e.code, path)
        return None
    except Exception as e:
        logging.warning("ネットワークエラー（リトライ上限）: %s", e)
        return None


def gitlab_get_list(host: str, token: str, path: str, params: dict | None = None) -> list[dict]:
    """GitLab REST API に対してページネーションを行い、全件を返す。一時的なネットワーク障害はリトライ。失敗時は空リスト。"""
    params = dict(params or {})
    params.setdefault("per_page", 100)
    all_results: list[dict] = []
    page = 1
    while True:
        params["page"] = page
        url = f"https://{host}/api/v4{path}?" + urllib.parse.urlencode(
            {k: v for k, v in params.items() if v is not None}
        )
        try:
            body, next_page = retry_on_network_error(
                _http_get, url, token, logger=logging.getLogger(__name__)
            )
            page_data = json.loads(body)
            if not isinstance(page_data, list):
                break
            all_results.extend(page_data)
            if not next_page:
                break
            page = int(next_page)
        except urllib.error.HTTPError as e:
            logging.warning("GitLab API HTTP %s: %s", e.code, path)
            break
        except Exception as e:
            logging.warning("ネットワークエラー（リトライ上限）: %s", e)
            break
    return all_results


def fetch_open_issues(host: str, token: str, project: str) -> list[dict]:
    """status:open + assignee:any のイシューを全件取得する（description フィールドを含む）。"""
    ep = urllib.parse.quote(project, safe="")
    return gitlab_get_list(host, token, f"/projects/{ep}/issues", params={
        "state":  "opened",
        "labels": "status:open,assignee:any",
    })


# ---------------------------------------------------------------------------
# モック CLI（--dry-run 時および config.mock_cli=true 時）
# ---------------------------------------------------------------------------

def run_mock_cli(issue: dict, prompt: str) -> None:
    """
    モック CLI: 実際のエージェント CLI の代わりにプロンプトをファイルに保存する。
    ~/.config/gitlab-idd/mock-prompts/{timestamp}-issue-{id}.md に書き出す。
    """
    timestamp = datetime.now().strftime("%Y%m%dT%H%M%S")
    issue_id = issue.get("iid", "unknown")
    mock_dir = get_config_dir() / "mock-prompts"
    mock_dir.mkdir(parents=True, exist_ok=True)
    out_file = mock_dir / f"{timestamp}-issue-{issue_id}.md"
    out_file.write_text(prompt, encoding="utf-8")
    logging.info("[MOCK CLI] プロンプト保存: %s", out_file)


# ---------------------------------------------------------------------------
# デスクトップ通知（best-effort）
# ---------------------------------------------------------------------------

def send_notification(title: str, message: str) -> None:
    try:
        match platform.system():
            case "Darwin":
                # AppleScript 文字列内の \ と " をエスケープ
                def _as_escape(s: str) -> str:
                    return s.replace("\\", "\\\\").replace('"', '\\"')
                subprocess.run(
                    ["osascript", "-e",
                     f'display notification "{_as_escape(message)}" with title "{_as_escape(title)}"'],
                    check=False, capture_output=True,
                )
            case "Linux":
                subprocess.run(["notify-send", title, message],
                               check=False, capture_output=True)
            case "Windows":
                # PowerShell シングルクォート文字列を使用（変数展開なし）
                # シングルクォート自体は '' でエスケープ
                def _ps_escape(s: str) -> str:
                    return s.replace("'", "''")
                ps = (
                    "Add-Type -AssemblyName System.Windows.Forms;"
                    "$n=New-Object System.Windows.Forms.NotifyIcon;"
                    "$n.Icon=[System.Drawing.SystemIcons]::Information;"
                    "$n.Visible=$true;"
                    f"$n.ShowBalloonTip(5000,'{_ps_escape(title)}','{_ps_escape(message)}',"
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
# ワーカー起動
# ---------------------------------------------------------------------------

def _cleanup_old_worker_files(max_keep: int = 20) -> None:
    """古いワーカーログ・プロンプトファイルを削除し、最新 max_keep 件だけ残す。"""
    log_dir = get_config_dir()
    for pattern in ("worker-issue-*.log", "worker-prompt-*.md"):
        files = sorted(log_dir.glob(pattern), key=lambda p: p.stat().st_mtime)
        for old_file in files[: max(0, len(files) - max_keep)]:
            try:
                old_file.unlink()
            except OSError:
                pass


def launch_agent_worker(
    issue: dict,
    repo: RepoConfig,
    cli: AgentCLI,
    *,
    use_mock: bool = False,
) -> None:
    """
    イシューを処理するためにエージェント CLI を非同期起動する。
    use_mock=True の場合はプロンプトをファイルに保存するだけ（副作用なし）。

    プロンプトはコマンドライン引数ではなく stdin（ファイルリダイレクト）で渡す。
    これにより OS の ARG_MAX 制限（Linux: 引数 1 つあたり 128KB）を回避する。
    """
    via_wsl_kiro = cli.via_wsl and cli.name == "kiro"
    prompt = build_worker_prompt(issue, repo, via_wsl_kiro=via_wsl_kiro)
    issue_id = issue.get("iid", "?")
    _cleanup_old_worker_files()

    if use_mock:
        run_mock_cli(issue, prompt)
        return

    # プロンプトをファイルに書き出す（stdin として渡す + デバッグ用に残す）
    prompt_file = get_config_dir() / f"worker-prompt-{issue_id}.md"
    prompt_file.write_text(prompt, encoding="utf-8")

    # stdin 経由でプロンプトを渡す（コマンドライン引数にプロンプトを含めない）
    cmd = cli.build_command_stdin()
    # WSL kiro はプロンプト内で clone するため cwd は不要
    cwd = None if cli.via_wsl else (repo.get("local_path") or ".")
    log_file = get_config_dir() / f"worker-issue-{issue_id}.log"
    logging.info("[%s] 起動: イシュー #%s  cwd=%s", cli.name, issue_id, cwd or "WSL")

    try:
        kwargs: dict = {
            "cwd":    cwd,
            "stderr": subprocess.STDOUT,
        }
        match platform.system():
            case "Windows":
                kwargs["creationflags"] = 0x00000008  # DETACHED_PROCESS
            case _:
                kwargs["start_new_session"] = True
        with open(log_file, "w", encoding="utf-8") as log_fh:
            kwargs["stdout"] = log_fh
            # stdin にプロンプトファイルを接続する
            # Unix では子プロセスが fd を継承するため、親が with を抜けて
            # ファイルを閉じた後も子プロセスは読み続けることができる
            with open(prompt_file, "r", encoding="utf-8") as prompt_fh:
                kwargs["stdin"] = prompt_fh
                subprocess.Popen(cmd, **kwargs)
    except Exception as e:
        logging.error("CLI 起動失敗 (イシュー #%s): %s", issue_id, e)


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


def mark_seen(config: DaemonConfig, repo: RepoConfig, issues: list[dict]) -> None:
    key = repo_key(repo)
    seen = set(config.setdefault("seen_issues", {}).get(key, []))
    for issue in issues:
        seen.add(issue["iid"])
    config["seen_issues"][key] = sorted(seen)


def run_poll_cycle(
    config: DaemonConfig,
    cli: AgentCLI,
    *,
    use_mock: bool = False,
) -> None:
    """
    全リポジトリに対して 1 サイクルのポーリングを実行する。
    use_mock=True の場合は GitLab API は実際に呼び出すが CLI はモックする。
    """
    repos = config.get("repos", [])
    if not repos:
        logging.debug("ポーリング対象リポジトリなし")
        return

    mock_tag = "[MOCK CLI] " if use_mock else ""

    for repo in repos:
        host = repo.get("host", "?")
        project = repo.get("project", "?")
        token = get_token_for_repo(repo)

        if not token:
            logging.warning("トークン未設定: %s/%s — スキップ", host, project)
            continue

        logging.debug("ポーリング中: %s/%s", host, project)

        try:
            all_issues = fetch_open_issues(host, token, project)
        except Exception as e:
            logging.error("ポーリングエラー %s/%s: %s", host, project, e)
            continue

        key = repo_key(repo)
        seen = set(config.get("seen_issues", {}).get(key, []))
        new_issues = [i for i in all_issues if i.get("iid") not in seen]

        if not new_issues:
            logging.debug("%s/%s: 新規イシューなし（全 %d 件確認済み）", host, project, len(all_issues))
            continue

        logging.info("%s%d 件の新規イシュー: %s/%s", mock_tag, len(new_issues), host, project)
        for issue in new_issues:
            iid = issue.get("iid", "?")
            title = issue.get("title", "")
            logging.info("  %sイシュー #%s: %s", mock_tag, iid, title)
            # ワーカー起動前に seen に記録することで、複数デーモンインスタンスによる
            # 重複起動を防ぐ（ベストエフォート）
            if not use_mock:
                mark_seen(config, repo, [issue])
                save_config(config)
            send_notification(
                f"GitLab 新規イシュー ({host}/{project})",
                f"#{iid} {title}",
            )
            launch_agent_worker(issue, repo, cli, use_mock=use_mock)

        if use_mock:
            logging.info("[MOCK CLI] seen_issues は更新しません（再実行でテスト可能）")


# ---------------------------------------------------------------------------
# メインループ
# ---------------------------------------------------------------------------

def setup_logging() -> None:
    log_path = get_log_path()
    log_path.parent.mkdir(parents=True, exist_ok=True)
    # RotatingFileHandler: 5MB × 最大 3 世代（計 15MB）でローテーション
    file_handler = logging.handlers.RotatingFileHandler(
        log_path,
        maxBytes=5 * 1024 * 1024,
        backupCount=3,
        encoding="utf-8",
    )
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)-8s %(message)s",
        handlers=[
            file_handler,
            logging.StreamHandler(sys.stdout),
        ],
    )


def main() -> None:
    parser = argparse.ArgumentParser(
        description="GitLab issue polling daemon (Python 3.11+, stdlib only)",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    parser.add_argument(
        "--once",    action="store_true",
        help="1 回ポーリングして終了",
    )
    parser.add_argument(
        "--dry-run", action="store_true",
        help="実 GitLab API + モック CLI でテスト（seen_issues 更新なし）",
    )
    parser.add_argument(
        "--interval", type=int, metavar="SECS",
        help="インターバル上書き（秒）",
    )
    args = parser.parse_args()

    setup_logging()
    logging.info("gitlab-idd poll daemon 起動 (pid=%s)", os.getpid())

    config = load_config()
    if args.interval:
        config["poll_interval_seconds"] = args.interval

    # CLI 決定: --dry-run / config.mock_cli は real CLI 不要
    use_mock = args.dry_run or config.get("mock_cli", False)
    preferred = config.get("preferred_cli")
    cli = find_best_agent_cli(preferred)

    if cli is None and not use_mock:
        logging.error(
            "エージェント CLI が見つかりません。"
            "claude / codex / kiro-cli / q のいずれかをインストールしてください。\n"
            "  または config.json に mock_cli: true を設定してモックモードで動作できます。"
        )
        sys.exit(1)

    if cli is None:
        # mock 専用ダミー CLI
        cli = AgentCLI("mock", "", [])

    if use_mock:
        logging.info(
            "モック CLI モード: GitLab API は実接続、CLI は mock-prompts/ に保存します"
        )
        logging.info("  mock-prompts: %s", get_config_dir() / "mock-prompts")

    logging.info("使用 CLI: %s%s", cli.name, " (WSL2経由)" if cli.via_wsl else "")
    logging.info("インターバル: %s 秒 / 設定: %s", config.get("poll_interval_seconds"), get_config_path())

    if args.once or args.dry_run:
        run_poll_cycle(config, cli, use_mock=use_mock)
        return

    interval = config.get("poll_interval_seconds", DEFAULT_POLL_INTERVAL)
    _cached_preferred: str | None = config.get("preferred_cli")
    while True:
        try:
            config = load_config()  # 毎サイクル再読み込み（設定変更を即反映）
            interval = config.get("poll_interval_seconds", DEFAULT_POLL_INTERVAL)
            use_mock = config.get("mock_cli", False)
            # preferred_cli が変更された場合のみ CLI を再検出する（サブプロセス起動コスト節約）
            new_preferred = config.get("preferred_cli")
            if new_preferred != _cached_preferred or cli is None:
                cli = find_best_agent_cli(new_preferred) or cli
                _cached_preferred = new_preferred
            run_poll_cycle(config, cli, use_mock=use_mock)
        except Exception as e:
            logging.error("予期しないエラー: %s", e)
        time.sleep(interval)


if __name__ == "__main__":
    main()
