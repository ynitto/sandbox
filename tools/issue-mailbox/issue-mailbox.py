#!/usr/bin/env python
"""
issue-mailbox.py — GitLab リポジトリのイシューを定期ポーリングし、
変化のあったものを tmux の特定ペインに送信するスクリプト。

依存ライブラリ:
  - tmux      (apt install tmux)     通知送信
  - requests  (pip install requests) GitLab API 呼び出し
  - PyYAML    (pip install pyyaml)   設定ファイル読み込み（JSON も可、任意）

動作環境: WSL (Ubuntu) / Linux
終了方法: Ctrl+C または SIGTERM

使い方:
  python issue-mailbox.py                           # 設定ファイルを自動検出して起動
  python issue-mailbox.py --config ~/issue-mailbox.yaml
  python issue-mailbox.py view                      # 通知ビューアを起動（別ペインで実行）
  python issue-mailbox.py status                    # 現在のポーリング状態を表示

設定ファイルの検索順序:
  1. --config で明示指定したパス
  2. カレントディレクトリの issue-mailbox.yaml / .yml / .json
  3. HOME の issue-mailbox.yaml / .yml / .json
"""

import argparse
import datetime
import json
import logging
import os
import re
import shutil
import signal
import subprocess
import sys
import threading
import time
from pathlib import Path
from typing import Any, Optional

# ── 依存チェック ────────────────────────────────────────────────────────────

if shutil.which("tmux") is None:
    print("[issue-mailbox] ERROR: tmux が見つかりません。", file=sys.stderr)
    print("  Ubuntu/WSL: sudo apt install tmux", file=sys.stderr)
    sys.exit(1)

try:
    import requests  # type: ignore
except ImportError:
    print("[issue-mailbox] ERROR: requests が必要です。", file=sys.stderr)
    print("  pip install requests", file=sys.stderr)
    sys.exit(1)

try:
    import yaml  # type: ignore

    def _load_config_file(path: Path) -> dict:
        with path.open(encoding="utf-8") as f:
            return yaml.safe_load(f) or {}

except ImportError:
    yaml = None  # type: ignore

    def _load_config_file(path: Path) -> dict:  # type: ignore[misc]
        if path.suffix.lower() in (".yaml", ".yml"):
            print("[issue-mailbox] ERROR: YAML 設定ファイルを読むには PyYAML が必要です。", file=sys.stderr)
            print("  pip install pyyaml", file=sys.stderr)
            sys.exit(1)
        with path.open(encoding="utf-8") as f:
            return json.load(f)


# ── ログ設定 ────────────────────────────────────────────────────────────────

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
    stream=sys.stderr,
)
log = logging.getLogger("issue-mailbox")

# ── デフォルトパス ──────────────────────────────────────────────────────────

_HOME_DIR = Path.home() / ".issue-mailbox"
_DEFAULT_STATE_DIR = _HOME_DIR / "state"
_DEFAULT_LOG_FILE = _HOME_DIR / "notifications.log"
_CONFIG_NAMES = ["issue-mailbox.yaml", "issue-mailbox.yml", "issue-mailbox.json"]


# ── 設定ファイル読み込み ─────────────────────────────────────────────────────

def _find_config() -> Optional[Path]:
    for name in _CONFIG_NAMES:
        for base in (Path.cwd(), Path.home()):
            p = base / name
            if p.exists():
                return p
    return None


def load_config(path: Optional[Path]) -> dict:
    if path is None:
        path = _find_config()
    if path is None:
        print("[issue-mailbox] ERROR: 設定ファイルが見つかりません。", file=sys.stderr)
        print("  issue-mailbox.yaml.example をコピーして設定してください。", file=sys.stderr)
        sys.exit(1)
    log.info("設定ファイル: %s", path)
    return _load_config_file(path)


# ── GitLab API クライアント ──────────────────────────────────────────────────

class GitLabClient:
    def __init__(self, gitlab_url: str, project_id: int, private_token: str) -> None:
        self.base = gitlab_url.rstrip("/")
        self.project_id = project_id
        self._session = requests.Session()
        self._session.headers["PRIVATE-TOKEN"] = private_token

    def get_issues(
        self,
        state: str = "opened",
        labels: Optional[list] = None,
        assignee_username: Optional[str] = None,
        per_page: int = 100,
    ) -> list[dict]:
        url = f"{self.base}/api/v4/projects/{self.project_id}/issues"
        params: dict[str, Any] = {
            "state": state,
            "per_page": per_page,
            "order_by": "updated_at",
            "sort": "desc",
        }
        if labels:
            params["labels"] = ",".join(labels)
        if assignee_username:
            params["assignee_username"] = assignee_username

        issues: list[dict] = []
        page = 1
        while True:
            params["page"] = page
            try:
                r = self._session.get(url, params=params, timeout=30)
                r.raise_for_status()
            except requests.RequestException as e:
                log.error("GitLab API エラー (project=%s): %s", self.project_id, e)
                return issues

            batch: list[dict] = r.json()
            if not batch:
                break
            issues.extend(batch)
            if len(batch) < per_page:
                break
            page += 1

        return issues


# ── 状態管理 ────────────────────────────────────────────────────────────────

def _state_path(state_dir: Path, repo_name: str) -> Path:
    state_dir.mkdir(parents=True, exist_ok=True)
    safe = re.sub(r"[^\w-]", "_", repo_name)
    return state_dir / f"{safe}.json"


def _load_state(state_dir: Path, repo_name: str) -> dict:
    p = _state_path(state_dir, repo_name)
    if not p.exists():
        return {"issues": {}}
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except Exception:
        return {"issues": {}}


def _save_state(state_dir: Path, repo_name: str, state: dict) -> None:
    _state_path(state_dir, repo_name).write_text(
        json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8"
    )


def _to_snapshot(issue: dict) -> dict:
    """イシューの変更検出に必要なフィールドだけ抽出する。"""
    assignee = issue.get("assignee") or {}
    return {
        "iid": issue["iid"],
        "title": issue["title"],
        "state": issue["state"],
        "labels": sorted(issue.get("labels") or []),
        "assignee": assignee.get("username", ""),
        "updated_at": issue.get("updated_at", ""),
        "url": issue.get("web_url", ""),
    }


# ── 変更検出 ────────────────────────────────────────────────────────────────

def detect_changes(
    repo_name: str,
    state_dir: Path,
    current_issues: list[dict],
    watch: dict,
    filter_state: str,
) -> list[str]:
    """前回との差分を検出して通知メッセージのリストを返す。"""
    old_state = _load_state(state_dir, repo_name)
    old_map: dict[str, dict] = old_state.get("issues", {})
    new_map: dict[str, dict] = {}
    notifications: list[str] = []

    for issue in current_issues:
        snap = _to_snapshot(issue)
        iid_str = str(snap["iid"])
        new_map[iid_str] = snap

        if iid_str not in old_map:
            # 新規イシュー
            if watch.get("new_issues", True):
                labels_part = f" [{', '.join(snap['labels'])}]" if snap["labels"] else ""
                assignee_part = f" @{snap['assignee']}" if snap["assignee"] else ""
                notifications.append(
                    f"[NEW] #{snap['iid']} {snap['title']}{labels_part}{assignee_part}"
                )
            continue

        old = old_map[iid_str]

        if watch.get("state_changes", True) and old["state"] != snap["state"]:
            notifications.append(
                f"[UPD] #{snap['iid']} state:{old['state']}→{snap['state']}  {snap['title']}"
            )

        if watch.get("title_changes", False) and old["title"] != snap["title"]:
            notifications.append(
                f"[UPD] #{snap['iid']} title changed  {snap['title']}"
            )

        if watch.get("label_changes", False) and old["labels"] != snap["labels"]:
            added = sorted(set(snap["labels"]) - set(old["labels"]))
            removed = sorted(set(old["labels"]) - set(snap["labels"]))
            parts = []
            if added:
                parts.append(f"+{','.join(added)}")
            if removed:
                parts.append(f"-{','.join(removed)}")
            notifications.append(
                f"[UPD] #{snap['iid']} labels:{' '.join(parts)}  {snap['title']}"
            )

        if watch.get("assignee_changes", True) and old["assignee"] != snap["assignee"]:
            old_a = old["assignee"] or "(未割り当て)"
            new_a = snap["assignee"] or "(未割り当て)"
            notifications.append(
                f"[UPD] #{snap['iid']} assignee:{old_a}→{new_a}  {snap['title']}"
            )

    # state="opened" フィルタ時にリストから消えたイシューはクローズされた
    if filter_state == "opened" and watch.get("state_changes", True):
        for iid_str, old in old_map.items():
            if iid_str not in new_map:
                notifications.append(
                    f"[CLOSED] #{old['iid']} {old['title']}"
                )

    # 状態ファイルを更新
    new_state = {
        "issues": new_map,
        "last_poll": datetime.datetime.now().isoformat(timespec="seconds"),
    }
    _save_state(state_dir, repo_name, new_state)
    return notifications


# ── 通知配信 ────────────────────────────────────────────────────────────────

def _tmux(*args: str) -> subprocess.CompletedProcess:
    return subprocess.run(["tmux"] + list(args), capture_output=True, text=True)


def deliver(
    message: str,
    log_file: Path,
    tmux_target: Optional[str],
    send_enter: bool,
) -> None:
    """通知をログファイルと tmux ペインに届ける。"""
    ts = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    line = f"{ts}  {message}"

    # ログファイル
    log_file.parent.mkdir(parents=True, exist_ok=True)
    with log_file.open("a", encoding="utf-8") as f:
        f.write(line + "\n")

    # tmux send-keys
    if tmux_target:
        keys = [line, "Enter"] if send_enter else [line]
        r = _tmux("send-keys", "-t", tmux_target, "--", *keys)
        if r.returncode != 0:
            log.warning("tmux send-keys 失敗 (target=%s): %s", tmux_target, r.stderr.strip())


# ── ポーリングスレッド ───────────────────────────────────────────────────────

def _poll_repo(
    repo_cfg: dict,
    state_dir: Path,
    log_file: Path,
    tmux_target: Optional[str],
    send_enter: bool,
    stop_event: threading.Event,
    first_run_silent: bool,
) -> None:
    name = repo_cfg["name"]
    client = GitLabClient(
        gitlab_url=repo_cfg["gitlab_url"],
        project_id=int(repo_cfg["project_id"]),
        private_token=repo_cfg["private_token"],
    )
    filter_state: str = repo_cfg.get("state", "opened")
    labels: list = repo_cfg.get("labels_filter") or []
    assignee: Optional[str] = repo_cfg.get("assignee_username") or None
    interval_sec: int = int(repo_cfg.get("poll_interval_minutes", 5)) * 60
    watch: dict = repo_cfg.get("watch", {})

    log.info("[%s] ポーリング開始 (interval=%d分)", name, interval_sec // 60)
    is_first = True

    while not stop_event.is_set():
        try:
            issues = client.get_issues(
                state=filter_state,
                labels=labels or None,
                assignee_username=assignee,
            )
            if is_first and first_run_silent:
                # 初回は状態を保存するだけで通知しない（既存イシューをノイズにしない）
                snap_map = {str(_to_snapshot(i)["iid"]): _to_snapshot(i) for i in issues}
                _save_state(state_dir, name, {
                    "issues": snap_map,
                    "last_poll": datetime.datetime.now().isoformat(timespec="seconds"),
                })
                log.info("[%s] 初回ポーリング完了: %d 件を記録", name, len(issues))
            else:
                changes = detect_changes(name, state_dir, issues, watch, filter_state)
                if changes:
                    log.info("[%s] %d 件の変化を検出", name, len(changes))
                for msg in changes:
                    deliver(f"[{name}] {msg}", log_file, tmux_target, send_enter)
        except Exception as e:
            log.error("[%s] ポーリングエラー: %s", name, e)

        is_first = False
        stop_event.wait(interval_sec)

    log.info("[%s] ポーリング終了", name)


# ── view モード ─────────────────────────────────────────────────────────────

def cmd_view(log_file: Path) -> None:
    """通知ログをリアルタイム表示する（専用ペインで実行）。"""
    print(f"[issue-mailbox] ビューア起動: {log_file}")
    print("[issue-mailbox] Ctrl+C で終了\n")

    # 過去ログの末尾 30 行を表示してから追跡開始
    if log_file.exists():
        lines = log_file.read_text(encoding="utf-8").splitlines()
        if lines:
            print("── 過去ログ (末尾30件) ─────────────────────────────────")
            for line in lines[-30:]:
                print(line)
            print("── ここから新着 ────────────────────────────────────────\n")
    else:
        print(f"(ログファイルが存在しません: {log_file})\n")

    # tail -f 相当
    try:
        log_file.parent.mkdir(parents=True, exist_ok=True)
        log_file.touch(exist_ok=True)

        with log_file.open(encoding="utf-8") as f:
            f.seek(0, 2)  # ファイル末尾にシーク
            while True:
                line = f.readline()
                if line:
                    print(line, end="", flush=True)
                else:
                    time.sleep(0.2)
    except KeyboardInterrupt:
        print("\n[issue-mailbox] ビューア終了")


# ── status ──────────────────────────────────────────────────────────────────

def cmd_status(config: dict, state_dir: Path) -> None:
    repos = config.get("repositories", [])
    tmux_cfg = config.get("tmux", {})
    target = tmux_cfg.get("target", "(なし)")
    log_file = Path(config.get("log_file", str(_DEFAULT_LOG_FILE))).expanduser()

    print(f"通知先 tmux ペイン: {target}")
    print(f"ログファイル      : {log_file}")
    print(f"リポジトリ数      : {len(repos)}\n")

    for repo in repos:
        name = repo["name"]
        enabled = repo.get("enabled", True)
        state = _load_state(state_dir, name)
        last = state.get("last_poll", "未ポーリング")
        count = len(state.get("issues", {}))
        status_mark = "○" if enabled else "✕"
        print(f"  {status_mark} {name}")
        print(f"      最終ポーリング: {last}  イシュー数: {count}")
        print(f"      GitLab: {repo['gitlab_url']}  project_id={repo['project_id']}")
        print(f"      state={repo.get('state','opened')}  interval={repo.get('poll_interval_minutes',5)}分")


# ── メイン ──────────────────────────────────────────────────────────────────

def main() -> None:
    parser = argparse.ArgumentParser(
        description="GitLab イシューを定期ポーリングして tmux ペインに変化を通知する",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
サブコマンド:
  run    (デフォルト) ポーリングデーモンを起動する
  view   通知ビューアを起動する（別ペインで実行することを推奨）
  status 現在のポーリング状態を表示する

使い方:
  # ポーリング起動
  python issue-mailbox.py --config ~/issue-mailbox.yaml

  # 別ペインで通知を表示
  python issue-mailbox.py view --config ~/issue-mailbox.yaml
""",
    )
    parser.add_argument(
        "command",
        nargs="?",
        default="run",
        choices=["run", "view", "status"],
        help="サブコマンド (デフォルト: run)",
    )
    parser.add_argument(
        "--config", "-c",
        type=Path,
        default=None,
        metavar="PATH",
        help="設定ファイルのパス (YAML または JSON)",
    )
    parser.add_argument(
        "--no-silent-first",
        action="store_true",
        help="初回ポーリング時も既存イシューを通知する（デフォルト: 初回は通知しない）",
    )
    args = parser.parse_args()

    config = load_config(args.config)

    state_dir = Path(config.get("state_dir", str(_DEFAULT_STATE_DIR))).expanduser()
    log_file = Path(config.get("log_file", str(_DEFAULT_LOG_FILE))).expanduser()

    if args.command == "view":
        cmd_view(log_file)
        return

    if args.command == "status":
        cmd_status(config, state_dir)
        return

    # ── run モード ─────────────────────────────────────────────────────────
    tmux_cfg = config.get("tmux", {})
    tmux_target: Optional[str] = tmux_cfg.get("target") if tmux_cfg else None
    send_enter: bool = bool(tmux_cfg.get("send_enter", False)) if tmux_cfg else False
    first_run_silent: bool = not args.no_silent_first

    repos = [r for r in config.get("repositories", []) if r.get("enabled", True)]
    if not repos:
        log.error("有効な repositories が設定ファイルに定義されていません")
        sys.exit(1)

    stop_event = threading.Event()

    def _shutdown(signum: int, frame: Any) -> None:
        log.info("終了シグナル受信 (signal=%d)、停止中...", signum)
        stop_event.set()

    signal.signal(signal.SIGINT, _shutdown)
    signal.signal(signal.SIGTERM, _shutdown)
    try:
        signal.signal(signal.SIGHUP, _shutdown)
    except AttributeError:
        pass  # Windows では SIGHUP が存在しない

    threads = []
    for repo_cfg in repos:
        t = threading.Thread(
            target=_poll_repo,
            args=(repo_cfg, state_dir, log_file, tmux_target, send_enter,
                  stop_event, first_run_silent),
            daemon=True,
            name=f"poll-{repo_cfg['name']}",
        )
        t.start()
        threads.append(t)

    log.info(
        "issue-mailbox 起動完了: %d リポジトリ監視中  ログ=%s",
        len(threads),
        log_file,
    )
    if tmux_target:
        log.info("通知先 tmux ペイン: %s (send_enter=%s)", tmux_target, send_enter)
    if first_run_silent:
        log.info("初回ポーリングは通知なし（--no-silent-first で変更可）")

    stop_event.wait()
    for t in threads:
        t.join(timeout=5)
    log.info("issue-mailbox 終了")


if __name__ == "__main__":
    main()
