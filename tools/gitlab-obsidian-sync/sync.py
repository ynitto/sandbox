#!/usr/bin/env python3
"""
gitlab-obsidian-sync — GitLab Issue を Obsidian ノートに同期するスクリプト。

機能:
  - 複数の GitLab リポジトリ (プロジェクト) を登録・管理
  - 定期ポーリング (各リポジトリごとに間隔を設定可能)
  - 手動同期コマンド
  - Issue → Obsidian MD ノート (frontmatter + 本文テンプレート)
  - status: ready の frontmatter フラグで Kiro 実行トリガーを制御
  - --kanban モード: GitLab Issues → Obsidian Kanban ボード (kanban.md) 出力

依存:
  - PyYAML    (pip install pyyaml)

使い方:
  python3 sync.py [--config CONFIG_FILE] [--once] [--sync REPO_NAME]
  python3 sync.py --kanban [--config CONFIG_FILE]   # Kanban ボードを生成して終了

インタラクティブコマンド (--once なしで起動した場合):
  sync [<repo>]       全リポジトリまたは指定リポジトリを今すぐ同期
  add <name> <url> <project-id> <token>
                      新しいリポジトリを登録して設定を保存
  list                登録済みリポジトリを表示
  status              最終同期時刻とステータスを表示
  interval <repo> <minutes>
                      ポーリング間隔を変更
  help                コマンド一覧
  quit                終了
"""

from __future__ import annotations

import argparse
import json
import logging
import re
import shlex
import sys
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

# ---------------------------------------------------------------------------
# 依存チェック
# ---------------------------------------------------------------------------

try:
    import yaml
except ImportError:
    print("[sync] ERROR: PyYAML が必要です。  pip install pyyaml", file=sys.stderr)
    sys.exit(1)

# ---------------------------------------------------------------------------
# ログ
# ---------------------------------------------------------------------------

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
    stream=sys.stderr,
)
log = logging.getLogger("gitlab-sync")

# ---------------------------------------------------------------------------
# 設定
# ---------------------------------------------------------------------------

DEFAULT_CONFIG_NAMES = ["gitlab-obsidian-sync.yaml", "gitlab-obsidian-sync.yml", "gitlab-obsidian-sync.json"]

DEFAULT_ISSUE_TEMPLATE = """\
---
issue_id: {issue_id}
title: "{title}"
project: "{project}"
state: {state}
labels: {labels}
author: "{author}"
created_at: {created_at}
updated_at: {updated_at}
url: "{url}"
status: pending
kiro_executed: false
---

# {title}

## Description

{description}

## Kiro Task

<!-- このセクションを編集して Kiro に実行させる内容を記述してください -->
<!-- status を "ready" に変更すると Kiro の実行トリガーとなります -->
"""


def find_default_config(cwd: Path) -> Path | None:
    for name in DEFAULT_CONFIG_NAMES:
        for base in (cwd, Path.home()):
            p = base / name
            if p.is_file():
                return p
    return None


def load_config(path: Path) -> dict[str, Any]:
    with path.open(encoding="utf-8") as f:
        if path.suffix.lower() in (".yaml", ".yml"):
            return yaml.safe_load(f) or {}
        return json.load(f)


def save_config(path: Path, cfg: dict[str, Any]) -> None:
    with path.open("w", encoding="utf-8") as f:
        yaml.dump(cfg, f, allow_unicode=True, default_flow_style=False, sort_keys=False)


# ---------------------------------------------------------------------------
# GitLab API クライアント
# ---------------------------------------------------------------------------

class GitLabClient:
    def __init__(self, base_url: str, project_id: str | int, token: str) -> None:
        self.base_url = base_url.rstrip("/")
        self.project_id = project_id
        self._headers = {"PRIVATE-TOKEN": token}

    def _get(self, url: str, params: dict[str, Any]) -> list[dict[str, Any]]:
        full_url = f"{url}?{urllib.parse.urlencode(params)}"
        req = urllib.request.Request(full_url, headers=self._headers)
        try:
            with urllib.request.urlopen(req, timeout=30) as resp:
                if resp.status >= 400:
                    raise RuntimeError(f"HTTP {resp.status}: {full_url}")
                return json.loads(resp.read().decode())
        except urllib.error.HTTPError as e:
            raise RuntimeError(f"HTTP {e.code}: {full_url}") from e

    def get_issues(
        self,
        state: str = "opened",
        labels: list[str] | None = None,
        per_page: int = 100,
        updated_after: str | None = None,
    ) -> list[dict[str, Any]]:
        url = f"{self.base_url}/api/v4/projects/{self.project_id}/issues"
        params: dict[str, Any] = {"state": state, "per_page": per_page}
        if labels:
            params["labels"] = ",".join(labels)
        if updated_after:
            params["updated_after"] = updated_after

        all_issues: list[dict[str, Any]] = []
        page = 1
        while True:
            params["page"] = page
            batch = self._get(url, params)
            if not batch:
                break
            all_issues.extend(batch)
            if len(batch) < per_page:
                break
            page += 1

        return all_issues


# ---------------------------------------------------------------------------
# Obsidian ノート生成・更新
# ---------------------------------------------------------------------------

FRONTMATTER_RE = re.compile(r"^---\n(.*?)\n---\n", re.DOTALL)


def _slugify(text: str) -> str:
    """ファイル名として安全な文字列に変換する。"""
    text = re.sub(r"[^\w\s\-ぁ-んァ-ン一-龥]", "", text)
    text = re.sub(r"\s+", "-", text.strip())
    return text[:80]


def _parse_frontmatter(content: str) -> dict[str, Any]:
    m = FRONTMATTER_RE.match(content)
    if not m:
        return {}
    try:
        return yaml.safe_load(m.group(1)) or {}
    except yaml.YAMLError:
        return {}


def _update_frontmatter(content: str, updates: dict[str, Any]) -> str:
    """frontmatter の特定キーを更新して返す。"""
    m = FRONTMATTER_RE.match(content)
    if not m:
        return content
    fm = yaml.safe_load(m.group(1)) or {}
    fm.update(updates)
    new_fm = yaml.dump(fm, allow_unicode=True, default_flow_style=False, sort_keys=False).rstrip()
    return f"---\n{new_fm}\n---\n" + content[m.end():]


def issue_to_note(issue: dict[str, Any], project_name: str, template: str) -> str:
    labels = [lbl["name"] if isinstance(lbl, dict) else lbl for lbl in issue.get("labels", [])]
    description = (issue.get("description") or "").strip() or "_説明なし_"
    title_safe = issue["title"].replace('"', '\\"')
    return template.format(
        issue_id=issue["iid"],
        title=title_safe,
        project=project_name,
        state=issue["state"],
        labels=json.dumps(labels, ensure_ascii=False),
        author=issue.get("author", {}).get("username", "unknown"),
        created_at=issue.get("created_at", ""),
        updated_at=issue.get("updated_at", ""),
        url=issue.get("web_url", ""),
        description=description,
    )


def write_note(note_path: Path, content: str) -> bool:
    """ノートを書き込む。既存ファイルの status/kiro_executed は保持する。
    Returns True if file was created/updated."""
    if note_path.exists():
        existing = note_path.read_text(encoding="utf-8")
        existing_fm = _parse_frontmatter(existing)
        # ユーザーが編集した可能性のあるフィールドは保持
        preserved = {}
        for key in ("status", "kiro_executed", "kiro_result"):
            if key in existing_fm:
                preserved[key] = existing_fm[key]
        if preserved:
            content = _update_frontmatter(content, preserved)
        # 差分がなければスキップ
        if existing.strip() == content.strip():
            return False

    note_path.parent.mkdir(parents=True, exist_ok=True)
    note_path.write_text(content, encoding="utf-8")
    return True


# ---------------------------------------------------------------------------
# リポジトリ同期状態
# ---------------------------------------------------------------------------

class RepoState:
    def __init__(self, cfg: dict[str, Any]) -> None:
        self.name: str = cfg["name"]
        self.gitlab_url: str = cfg.get("gitlab_url", "https://gitlab.com")
        self.project_id: str | int = cfg["project_id"]
        self.token: str = cfg.get("private_token", "")
        self.poll_minutes: float = float(cfg.get("poll_interval_minutes", 5))
        self.labels_filter: list[str] = cfg.get("labels_filter", [])
        self.state_filter: str = cfg.get("state", "opened")
        self.output_dir: str = cfg.get("output_dir", f"issues/{self.name}")

        self.last_sync: datetime | None = None
        self.last_error: str | None = None
        self.synced_count: int = 0
        self._lock = threading.Lock()
        self._client: GitLabClient | None = None

    @property
    def client(self) -> GitLabClient:
        if self._client is None:
            self._client = GitLabClient(self.gitlab_url, self.project_id, self.token)
        return self._client

    def invalidate_client(self) -> None:
        self._client = None

    def to_config_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "gitlab_url": self.gitlab_url,
            "project_id": self.project_id,
            "private_token": self.token,
            "poll_interval_minutes": self.poll_minutes,
            "labels_filter": self.labels_filter,
            "state": self.state_filter,
            "output_dir": self.output_dir,
        }


# ---------------------------------------------------------------------------
# 同期エンジン
# ---------------------------------------------------------------------------

class SyncEngine:
    def __init__(self, vault_path: Path, template: str, config_path: Path) -> None:
        self.vault_path = vault_path
        self.template = template
        self.config_path = config_path
        self.repos: dict[str, RepoState] = {}
        self._threads: dict[str, threading.Thread] = {}
        self._stop_events: dict[str, threading.Event] = {}
        self._global_stop = threading.Event()

    def add_repo(self, cfg: dict[str, Any]) -> RepoState:
        repo = RepoState(cfg)
        self.repos[repo.name] = repo
        return repo

    def remove_repo(self, name: str) -> None:
        self.stop_polling(name)
        self.repos.pop(name, None)

    # ── 同期 ──

    def sync_repo(self, repo: RepoState) -> tuple[int, int]:
        """リポジトリを同期する。(作成数, 更新数) を返す。"""
        with repo._lock:
            try:
                issues = repo.client.get_issues(
                    state=repo.state_filter,
                    labels=repo.labels_filter or None,
                )
                created = updated = 0
                for issue in issues:
                    content = issue_to_note(issue, repo.name, self.template)
                    filename = f"{issue['iid']:05d}-{_slugify(issue['title'])}.md"
                    note_path = self.vault_path / repo.output_dir / filename
                    existed = note_path.exists()
                    changed = write_note(note_path, content)
                    if changed:
                        if existed:
                            updated += 1
                        else:
                            created += 1

                repo.last_sync = datetime.now(timezone.utc)
                repo.last_error = None
                repo.synced_count = len(issues)
                log.info("[%s] 同期完了: %d 件 (新規 %d, 更新 %d)", repo.name, len(issues), created, updated)
                return created, updated
            except Exception as exc:
                repo.last_error = str(exc)
                log.error("[%s] 同期エラー: %s", repo.name, exc)
                raise

    def sync_all(self) -> None:
        for repo in list(self.repos.values()):
            try:
                self.sync_repo(repo)
            except Exception:
                pass  # エラーはログ済み

    # ── ポーリング ──

    def start_polling(self, repo: RepoState) -> None:
        if repo.name in self._threads and self._threads[repo.name].is_alive():
            return
        stop_ev = threading.Event()
        self._stop_events[repo.name] = stop_ev

        def _loop() -> None:
            log.info("[%s] ポーリング開始 (%.1f 分間隔)", repo.name, repo.poll_minutes)
            while not stop_ev.is_set() and not self._global_stop.is_set():
                try:
                    self.sync_repo(repo)
                except Exception:
                    pass
                stop_ev.wait(timeout=repo.poll_minutes * 60)
            log.info("[%s] ポーリング停止", repo.name)

        t = threading.Thread(target=_loop, name=f"poll-{repo.name}", daemon=True)
        self._threads[repo.name] = t
        t.start()

    def stop_polling(self, name: str) -> None:
        ev = self._stop_events.pop(name, None)
        if ev:
            ev.set()

    def start_all_polling(self) -> None:
        for repo in self.repos.values():
            self.start_polling(repo)

    def stop_all(self) -> None:
        self._global_stop.set()
        for ev in self._stop_events.values():
            ev.set()

    def update_interval(self, name: str, minutes: float) -> None:
        repo = self.repos.get(name)
        if not repo:
            raise KeyError(f"リポジトリが見つかりません: {name}")
        repo.poll_minutes = minutes
        # スレッドを再起動して新しい間隔を反映
        self.stop_polling(name)
        time.sleep(0.1)
        self.start_polling(repo)

    # ── 状態表示 ──

    def print_status(self) -> None:
        if not self.repos:
            print("  (登録済みリポジトリなし)")
            return
        now = datetime.now(timezone.utc)
        for name, repo in self.repos.items():
            if repo.last_sync:
                age = (now - repo.last_sync).total_seconds()
                age_str = f"{age:.0f}s 前"
            else:
                age_str = "未同期"
            err_str = f"  ⚠ {repo.last_error}" if repo.last_error else ""
            polling = "🔄" if (name in self._threads and self._threads[name].is_alive()) else "⏸"
            print(
                f"  {polling} {name:20s}  最終同期: {age_str:12s}"
                f"  {repo.synced_count:4d} 件  {repo.poll_minutes:.0f}min{err_str}"
            )

    def print_list(self) -> None:
        if not self.repos:
            print("  (登録済みリポジトリなし)")
            return
        for name, repo in self.repos.items():
            print(f"  {name}")
            print(f"    URL:        {repo.gitlab_url}")
            print(f"    Project ID: {repo.project_id}")
            print(f"    State:      {repo.state_filter}")
            print(f"    Labels:     {repo.labels_filter or '(なし)'}")
            print(f"    Output:     {repo.output_dir}")
            print(f"    Interval:   {repo.poll_minutes} 分")


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

HELP_TEXT = """\
利用可能なコマンド:
  sync [<repo>]          全リポジトリまたは指定リポジトリを今すぐ同期
  add <name> <url> <project-id> <token>
                         新しいリポジトリを登録
  remove <repo>          リポジトリを削除
  list                   登録済みリポジトリを一覧表示
  status                 各リポジトリの同期状況を表示
  interval <repo> <min>  ポーリング間隔を変更 (分)
  help                   このヘルプを表示
  quit / exit            終了
"""


def run_interactive(engine: SyncEngine, config: dict[str, Any], config_path: Path) -> None:
    print("GitLab Obsidian Sync が起動しました。'help' でコマンド一覧を表示します。")
    engine.start_all_polling()

    try:
        while True:
            try:
                line = input("> ").strip()
            except (EOFError, KeyboardInterrupt):
                break
            if not line:
                continue

            try:
                parts = shlex.split(line)
            except ValueError as e:
                print(f"[parse error] {e}")
                continue

            cmd = parts[0].lower()

            if cmd in ("quit", "exit", "q"):
                break

            elif cmd == "help":
                print(HELP_TEXT)

            elif cmd == "sync":
                if len(parts) >= 2:
                    name = parts[1]
                    repo = engine.repos.get(name)
                    if not repo:
                        print(f"[error] リポジトリが見つかりません: {name}")
                    else:
                        print(f"[sync] {name} を同期中...")
                        try:
                            c, u = engine.sync_repo(repo)
                            print(f"[sync] 完了: 新規 {c} 件, 更新 {u} 件")
                        except Exception as e:
                            print(f"[error] {e}")
                else:
                    print("[sync] 全リポジトリを同期中...")
                    engine.sync_all()
                    print("[sync] 完了")

            elif cmd == "add":
                if len(parts) < 5:
                    print("使い方: add <name> <gitlab-url> <project-id> <token>")
                    continue
                _, name, url, project_id, token = parts[:5]
                if name in engine.repos:
                    print(f"[error] 既に登録されています: {name}")
                    continue
                cfg_entry: dict[str, Any] = {
                    "name": name,
                    "gitlab_url": url,
                    "project_id": project_id,
                    "private_token": token,
                }
                repo = engine.add_repo(cfg_entry)
                engine.start_polling(repo)
                # 設定ファイルに保存
                config.setdefault("repositories", []).append(repo.to_config_dict())
                save_config(config_path, config)
                print(f"[add] 登録完了: {name}  (設定を {config_path} に保存しました)")

            elif cmd == "remove":
                if len(parts) < 2:
                    print("使い方: remove <repo>")
                    continue
                name = parts[1]
                if name not in engine.repos:
                    print(f"[error] リポジトリが見つかりません: {name}")
                    continue
                engine.remove_repo(name)
                repos_cfg = config.get("repositories", [])
                config["repositories"] = [r for r in repos_cfg if r.get("name") != name]
                save_config(config_path, config)
                print(f"[remove] 削除完了: {name}")

            elif cmd == "list":
                engine.print_list()

            elif cmd == "status":
                engine.print_status()

            elif cmd == "interval":
                if len(parts) < 3:
                    print("使い方: interval <repo> <minutes>")
                    continue
                name, mins_str = parts[1], parts[2]
                try:
                    mins = float(mins_str)
                    if mins <= 0:
                        raise ValueError
                except ValueError:
                    print("[error] minutes は正の数で指定してください")
                    continue
                try:
                    engine.update_interval(name, mins)
                    # 設定保存
                    for r in config.get("repositories", []):
                        if r.get("name") == name:
                            r["poll_interval_minutes"] = mins
                    save_config(config_path, config)
                    print(f"[interval] {name} のポーリング間隔を {mins} 分に変更しました")
                except KeyError as e:
                    print(f"[error] {e}")

            else:
                print(f"[error] 不明なコマンド: {cmd}  ('help' でコマンド一覧)")

    finally:
        print("停止中…")
        engine.stop_all()


# ---------------------------------------------------------------------------
# Kanban ボード生成
# ---------------------------------------------------------------------------

# カラム定義: (カラム見出し, 対象ラベル, 対象state)
KANBAN_COLUMNS = [
    ("🆕 Todo",        "todo",    "opened"),
    ("⚡ In Progress", "doing",   "opened"),
    ("⏸ Waiting",     "waiting", "opened"),
    ("✅ Done",        "done",    "closed"),
    ("❌ Failed",      "failed",  "closed"),
]

PRIORITY_EMOJI = {"high": "🔴", "med": "🟡", "low": "🟢"}


def _issue_priority_emoji(labels: list[str]) -> str:
    for lbl in labels:
        for key, emoji in PRIORITY_EMOJI.items():
            if f"priority:{key}" in lbl:
                return emoji
    return ""


def _issue_source(labels: list[str]) -> str:
    for lbl in labels:
        if lbl.startswith("source:"):
            return lbl.split(":", 1)[1]
    return ""


def _result_summary(description: str) -> str:
    """description の '## 実行結果' セクションから1行抽出する。"""
    in_section = False
    for line in description.splitlines():
        if re.match(r"^##\s+実行結果", line):
            in_section = True
            continue
        if in_section:
            stripped = line.strip()
            if stripped and not stripped.startswith("#"):
                return stripped[:80]
            if stripped.startswith("#"):
                break
    return ""


def _format_card(issue: dict[str, Any], gitlab_url: str, project: str) -> str:
    labels = [lbl["name"] if isinstance(lbl, dict) else lbl for lbl in issue.get("labels", [])]
    iid = issue["iid"]
    title = issue["title"]
    url = issue.get("web_url") or f"{gitlab_url}/{project}/-/issues/{iid}"
    priority = _issue_priority_emoji(labels)
    source = _issue_source(labels)
    created = (issue.get("created_at") or "")[:10]
    desc = (issue.get("description") or "")
    result = _result_summary(desc)

    meta_parts = []
    if priority:
        meta_parts.append(f"優先度: {priority}")
    if source:
        meta_parts.append(f"ソース: {source}")
    if created:
        meta_parts.append(f"作成: {created}")
    meta = " | ".join(meta_parts)

    lines = [f"- [ ] **[[{url}|#{iid}]] {title}**"]
    if meta:
        lines.append(f"  {meta}")
    if result:
        lines.append(f"  > {result}")
    return "\n".join(lines)


def build_kanban_md(issues: list[dict[str, Any]], gitlab_url: str, project: str) -> str:
    """Issues リストから Obsidian Kanban プラグイン形式の Markdown を生成する。"""
    # カラムごとに振り分け
    buckets: dict[str, list[dict[str, Any]]] = {col[0]: [] for col in KANBAN_COLUMNS}

    for issue in issues:
        labels = [lbl["name"] if isinstance(lbl, dict) else lbl for lbl in issue.get("labels", [])]
        state = issue.get("state", "opened")
        assigned = False
        for col_name, col_label, col_state in KANBAN_COLUMNS:
            if state == col_state and col_label in labels:
                buckets[col_name].append(issue)
                assigned = True
                break
        if not assigned:
            # ラベルなし opened は Todo 扱い
            if state == "opened":
                buckets["🆕 Todo"].append(issue)

    lines = ["---", "kanban-plugin: board", "---", ""]

    for col_name, _, _ in KANBAN_COLUMNS:
        lines.append(f"## {col_name}")
        lines.append("")
        for issue in buckets[col_name]:
            lines.append(_format_card(issue, gitlab_url, project))
        lines.append("")
        # Done カラムの後に Complete マーカー
        if col_name == "✅ Done":
            lines.append("**Complete**")
            lines.append("")

    lines += [
        '%% kanban:settings',
        '```',
        '{"kanban-plugin":"board","list-collapse":[false,false,false,true,false]}',
        '```',
        '%%',
    ]
    return "\n".join(lines)


def run_kanban(engine: "SyncEngine", config: dict[str, Any]) -> None:
    """全リポジトリの Issues を取得して kanban.md を生成する。"""
    global_cfg = config.get("global", {})
    vault_path = Path(global_cfg.get("vault_path", ".")).expanduser()
    kanban_cfg = config.get("kanban", {})
    target_file = kanban_cfg.get("target_file", "AI-Tasks/kanban.md")
    label_filter = kanban_cfg.get("label_filter", [])

    all_issues: list[dict[str, Any]] = []
    gitlab_url = ""
    project = ""

    for repo in engine.repos.values():
        try:
            labels = label_filter or repo.labels_filter or None
            # opened + closed 両方取得
            for state in ("opened", "closed"):
                issues = repo.client.get_issues(state=state, labels=labels)
                all_issues.extend(issues)
            gitlab_url = repo.gitlab_url
            project_encoded = str(repo.project_id)
            # web_url からプロジェクトパスを取得
            if all_issues:
                sample_url = all_issues[0].get("web_url", "")
                m = re.match(r"(https?://[^/]+)/(.+)/-/issues/", sample_url)
                if m:
                    gitlab_url = m.group(1)
                    project = m.group(2)
            log.info("[kanban] %s: %d 件取得", repo.name, len(all_issues))
        except Exception as exc:
            log.error("[kanban] %s 取得エラー: %s", repo.name, exc)

    kanban_md = build_kanban_md(all_issues, gitlab_url, project)
    out_path = vault_path / target_file
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(kanban_md, encoding="utf-8")
    print(f"kanban.md を生成しました: {out_path}  ({len(all_issues)} 件)")


# ---------------------------------------------------------------------------
# エントリポイント
# ---------------------------------------------------------------------------

def main() -> None:
    parser = argparse.ArgumentParser(description="GitLab Issue を Obsidian ノートに同期します")
    parser.add_argument("--config", metavar="FILE", help="設定ファイルのパス")
    parser.add_argument("--once", action="store_true", help="1 回だけ同期して終了")
    parser.add_argument("--sync", metavar="REPO", help="指定リポジトリを同期して終了")
    parser.add_argument("--kanban", action="store_true", help="Kanban ボード (kanban.md) を生成して終了")
    args = parser.parse_args()

    # 設定ファイルの検索
    cwd = Path.cwd()
    if args.config:
        config_path = Path(args.config)
        if not config_path.is_file():
            log.error("設定ファイルが見つかりません: %s", config_path)
            sys.exit(1)
    else:
        config_path = find_default_config(cwd)
        if config_path is None:
            log.error(
                "設定ファイルが見つかりません。%s のいずれかを作成するか --config で指定してください。",
                DEFAULT_CONFIG_NAMES,
            )
            sys.exit(1)

    log.info("設定ファイル: %s", config_path)
    config = load_config(config_path)

    # グローバル設定
    global_cfg = config.get("global", {})
    vault_path = Path(global_cfg.get("vault_path", ".")).expanduser()
    global_poll = float(global_cfg.get("poll_interval_minutes", 5))
    template = global_cfg.get("issue_template", DEFAULT_ISSUE_TEMPLATE)

    if not vault_path.is_dir():
        log.error("vault_path が見つかりません: %s", vault_path)
        sys.exit(1)

    engine = SyncEngine(vault_path, template, config_path)

    # リポジトリ登録
    for repo_cfg in config.get("repositories", []):
        # グローバルのポーリング間隔をデフォルトに
        repo_cfg.setdefault("poll_interval_minutes", global_poll)
        engine.add_repo(repo_cfg)

    if not engine.repos:
        log.warning("リポジトリが登録されていません。'add' コマンドで追加してください。")

    # --sync: 指定リポジトリを 1 回同期して終了
    if args.sync:
        repo = engine.repos.get(args.sync)
        if not repo:
            log.error("リポジトリが見つかりません: %s", args.sync)
            sys.exit(1)
        c, u = engine.sync_repo(repo)
        print(f"同期完了: 新規 {c} 件, 更新 {u} 件")
        return

    # --once: 全リポジトリを 1 回同期して終了
    if args.once:
        engine.sync_all()
        return

    # --kanban: Kanban ボードを生成して終了
    if args.kanban:
        run_kanban(engine, config)
        return

    # インタラクティブモード (ポーリング + コマンドプロンプト)
    run_interactive(engine, config, config_path)


if __name__ == "__main__":
    main()
