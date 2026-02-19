#!/usr/bin/env python3
"""
VSCode Copilot / Claude Code Chat History Extractor

VSCode の workspaceStorage から Copilot チャット履歴、または Claude Code の
セッション履歴を取得・フィルタリングし、スキル生成のためのパターン分析を支援する。

使用例:
  python extract-copilot-history.py --days 90 --noise-filter
  python extract-copilot-history.py --workspace "/path/to/project" --days 30
  python extract-copilot-history.py --storage /custom/path/workspaceStorage
  python extract-copilot-history.py --since 2026-02-12T00:00:00Z --noise-filter
  python extract-copilot-history.py --source claude-code --noise-filter
"""

import argparse
import json
import os
import re
import sqlite3
import sys
from datetime import datetime, timedelta
from pathlib import Path


# ── ストレージパス取得 ──────────────────────────────────────────────────────────

def get_vscode_storage_path() -> Path:
    """OSに応じた workspaceStorage のデフォルトパスを返す。"""
    if sys.platform == "darwin":
        return Path.home() / "Library" / "Application Support" / "Code" / "User" / "workspaceStorage"
    elif sys.platform == "win32":
        appdata = os.environ.get("APPDATA", "")
        return Path(appdata) / "Code" / "User" / "workspaceStorage"
    else:  # Linux / その他
        return Path.home() / ".config" / "Code" / "User" / "workspaceStorage"


def get_claude_code_projects_path() -> Path:
    """OSに応じた Claude Code プロジェクト履歴のデフォルトパスを返す。"""
    return Path.home() / ".claude" / "projects"


# ── Claude Code セッション読み込み ────────────────────────────────────────────

def load_claude_code_sessions(projects_path: Path, workspace_filter: str = None,
                               since_ts: float = 0) -> list:
    """~/.claude/projects/ 以下の *.jsonl セッションファイルを読み込む。"""
    if not projects_path.exists():
        return []

    results = []
    for project_dir in sorted(projects_path.iterdir()):
        if not project_dir.is_dir():
            continue

        project_name = project_dir.name
        if workspace_filter and workspace_filter.lower() not in project_name.lower():
            continue

        for jsonl_file in sorted(project_dir.glob("*.jsonl")):
            mtime = jsonl_file.stat().st_mtime
            if since_ts and mtime < since_ts:
                continue

            messages = []
            try:
                with open(jsonl_file, encoding="utf-8") as f:
                    for line in f:
                        line = line.strip()
                        if not line:
                            continue
                        try:
                            entry = json.loads(line)
                        except json.JSONDecodeError:
                            continue
                        # Claude Code の JSONL 形式: {"role": "user", "content": "..."}
                        role = entry.get("role", "")
                        if role != "user":
                            continue
                        content = entry.get("content", "")
                        if isinstance(content, list):
                            # content がブロック配列の場合、テキストブロックを結合
                            text = " ".join(
                                b.get("text", "") for b in content
                                if isinstance(b, dict) and b.get("type") == "text"
                            )
                        else:
                            text = str(content)
                        text = text.strip()
                        if text:
                            messages.append({"text": text, "timestamp": mtime})
            except IOError:
                continue

            if messages:
                results.append({
                    "source": str(jsonl_file),
                    "session_id": jsonl_file.stem,
                    "workspace": project_name,
                    "messages": messages,
                    "mtime": mtime,
                })

    return results


# ── ワークスペース識別 ─────────────────────────────────────────────────────────

def get_workspace_name(workspace_dir: Path) -> str:
    """workspace.json からプロジェクトパスを取得する。"""
    workspace_json = workspace_dir / "workspace.json"
    if workspace_json.exists():
        try:
            with open(workspace_json, encoding="utf-8") as f:
                data = json.load(f)
            # file:///path/to/project 形式
            folder = data.get("folder", data.get("workspace", ""))
            if folder:
                folder = re.sub(r"^file://", "", folder)
                return folder
        except (json.JSONDecodeError, IOError):
            pass
    return str(workspace_dir.name)


# ── セッション読み込み ─────────────────────────────────────────────────────────

def load_from_chat_sessions(workspace_dir: Path) -> list:
    """chatSessions/*.json からセッションを読み込む（新形式）。"""
    sessions = []
    chat_dir = workspace_dir / "chatSessions"
    if not chat_dir.exists():
        return sessions

    for json_file in sorted(chat_dir.glob("*.json")):
        try:
            with open(json_file, encoding="utf-8") as f:
                data = json.load(f)
            sessions.append({
                "source": str(json_file),
                "session_id": json_file.stem,
                "data": data,
                "mtime": json_file.stat().st_mtime,
            })
        except (json.JSONDecodeError, IOError):
            pass
    return sessions


def load_from_state_db(workspace_dir: Path) -> list:
    """state.vscdb (SQLite) からセッションを読み込む（旧形式フォールバック）。"""
    db_path = workspace_dir / "state.vscdb"
    if not db_path.exists():
        return []

    sessions = []
    try:
        conn = sqlite3.connect(str(db_path))
        cursor = conn.cursor()
        for key in ("interactive.sessions", "memento/interactive-session"):
            cursor.execute(
                "SELECT value FROM ItemTable WHERE key = ?", (key,)
            )
            row = cursor.fetchone()
            if row:
                data = json.loads(row[0])
                sessions.append({
                    "source": f"{db_path}[{key}]",
                    "session_id": key,
                    "data": data,
                    "mtime": db_path.stat().st_mtime,
                })
                break
        conn.close()
    except (sqlite3.Error, json.JSONDecodeError, IOError):
        pass
    return sessions


# ── メッセージ抽出 ─────────────────────────────────────────────────────────────

def extract_user_messages(session_data) -> list:
    """セッションデータからユーザーメッセージを抽出する。

    VSCode Copilot のデータ構造は複数バージョンにわたって異なるため、
    複数パターンに対応する。
    """
    messages = []

    if isinstance(session_data, list):
        # リスト = 複数セッションの配列
        for item in session_data:
            messages.extend(extract_user_messages(item))

    elif isinstance(session_data, dict):
        # パターン1: {"requests": [{"message": {"text": "..."}, "timestamp": ...}]}
        if "requests" in session_data:
            for req in session_data.get("requests", []):
                msg = req.get("message", {})
                text = msg.get("text", "") if isinstance(msg, dict) else str(msg)
                timestamp = req.get("timestamp", 0)
                if text and not text.startswith("<"):
                    messages.append({"text": text.strip(), "timestamp": timestamp})

        # パターン2: {"exchanges": [{"human": "..."}]} 等
        for key in ("exchanges", "turns", "conversation", "history"):
            if key in session_data:
                for turn in session_data[key]:
                    if not isinstance(turn, dict):
                        continue
                    text = turn.get("human", turn.get("user", turn.get("request", "")))
                    if isinstance(text, dict):
                        text = text.get("text", "")
                    text = str(text).strip()
                    if text and not text.startswith("<"):
                        messages.append({
                            "text": text,
                            "timestamp": turn.get("timestamp", 0),
                        })
                break

        # パターン3: ネストされた sessions キー
        if "sessions" in session_data:
            for session in session_data.get("sessions", {}).values() if isinstance(
                session_data["sessions"], dict
            ) else session_data["sessions"]:
                messages.extend(extract_user_messages(session))

    return messages


# ── ノイズフィルタ ─────────────────────────────────────────────────────────────

NOISE_PATTERNS = [
    r"^/clear$",
    r"^/resume",
    r"^/help",
    r"^@workspace",
    r"^#",
    r"^\s*$",
]


def is_noise(text: str) -> bool:
    """コマンドや空メッセージ等のノイズを判定する。"""
    text = text.strip()
    return any(re.match(p, text) for p in NOISE_PATTERNS)


# ── メイン ────────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(
        description="VSCode Copilot / Claude Code チャット履歴をスキル生成用に抽出・フィルタリングする"
    )
    parser.add_argument("--workspace", help="ワークスペースパスのサブ文字列でフィルタ")
    parser.add_argument(
        "--days", type=int, default=90,
        help="過去 N 日分のみ対象にする（デフォルト: 90）。--since と併用時は --since が優先される"
    )
    parser.add_argument(
        "--since",
        help="この日時以降のセッションのみ対象にする（ISO 8601形式、例: 2026-02-12T00:00:00Z）。"
             "指定すると --days は無視される"
    )
    parser.add_argument("--storage", help="workspaceStorage の代替パスを指定")
    parser.add_argument(
        "--noise-filter", action="store_true",
        help="コマンド形式のメッセージを除外する"
    )
    parser.add_argument(
        "--max-sessions", type=int, default=50,
        help="ワークスペースごとの最大セッション数（デフォルト: 50）"
    )
    parser.add_argument(
        "--source", choices=["copilot", "claude-code", "auto"], default="auto",
        help="履歴ソース: copilot（VSCode Copilot）/ claude-code（Claude Code）/ auto（両方）"
    )
    args = parser.parse_args()

    # --since の解析（--days より優先）
    since_ts = 0.0
    if args.since:
        try:
            since_dt = datetime.fromisoformat(args.since.replace("Z", "+00:00"))
            since_ts = since_dt.timestamp()
            print(f"[INFO] --since: {args.since} 以降のセッションを対象にします")
        except ValueError:
            print(f"[ERROR] --since の形式が不正です: {args.since}", file=sys.stderr)
            print("  ISO 8601 形式で指定してください（例: 2026-02-12T00:00:00Z）", file=sys.stderr)
            sys.exit(1)
    elif args.days:
        since_ts = (datetime.now() - timedelta(days=args.days)).timestamp()

    total_sessions = 0
    total_messages = 0

    # ── VSCode Copilot 履歴 ──
    if args.source in ("copilot", "auto"):
        storage_path = Path(args.storage) if args.storage else get_vscode_storage_path()

        if not storage_path.exists():
            if args.source == "copilot":
                print(f"[ERROR] workspaceStorage が見つかりません: {storage_path}", file=sys.stderr)
                sys.exit(1)
            else:
                print(f"[INFO] VSCode Copilot 履歴が見つかりません（スキップ）: {storage_path}")
        else:
            workspace_dirs = [d for d in storage_path.iterdir() if d.is_dir()]

            for ws_dir in workspace_dirs:
                ws_name = get_workspace_name(ws_dir)

                if args.workspace and args.workspace.lower() not in ws_name.lower():
                    continue

                sessions = load_from_chat_sessions(ws_dir)
                if not sessions:
                    sessions = load_from_state_db(ws_dir)
                if not sessions:
                    continue

                if since_ts:
                    sessions = [s for s in sessions if s["mtime"] >= since_ts]
                sessions = sorted(sessions, key=lambda s: s["mtime"], reverse=True)[: args.max_sessions]

                if not sessions:
                    continue

                print(f"\n=== [Copilot] Workspace: {ws_name} ===")
                print(f"Sessions: {len(sessions)}")

                ws_messages = 0
                for session in sessions:
                    messages = extract_user_messages(session["data"])
                    if args.noise_filter:
                        messages = [m for m in messages if not is_noise(m["text"])]
                    if not messages:
                        continue

                    ts = datetime.fromtimestamp(session["mtime"]).strftime("%Y-%m-%d %H:%M")
                    print(f"\n  --- Session: {session['session_id'][:24]}... ({ts}) ---")
                    print(f"  Messages: {len(messages)}")
                    for msg in messages:
                        text_single = " ".join(msg["text"].splitlines())
                        truncated = text_single[:300] + "..." if len(text_single) > 300 else text_single
                        print(f"  - {truncated}")

                    ws_messages += len(messages)
                    total_sessions += 1

                total_messages += ws_messages

    # ── Claude Code 履歴 ──
    if args.source in ("claude-code", "auto"):
        cc_path = get_claude_code_projects_path()
        cc_sessions = load_claude_code_sessions(cc_path, args.workspace, since_ts)

        if not cc_sessions and args.source == "claude-code":
            print(f"[INFO] Claude Code 履歴が見つかりません: {cc_path}")
        elif cc_sessions:
            # プロジェクトごとにまとめて表示
            by_project: dict = {}
            for s in cc_sessions:
                by_project.setdefault(s["workspace"], []).append(s)

            for project_name, sessions in by_project.items():
                sessions = sorted(sessions, key=lambda s: s["mtime"], reverse=True)[: args.max_sessions]
                print(f"\n=== [Claude Code] Project: {project_name} ===")
                print(f"Sessions: {len(sessions)}")

                for session in sessions:
                    messages = session["messages"]
                    if args.noise_filter:
                        messages = [m for m in messages if not is_noise(m["text"])]
                    if not messages:
                        continue

                    ts = datetime.fromtimestamp(session["mtime"]).strftime("%Y-%m-%d %H:%M")
                    print(f"\n  --- Session: {session['session_id'][:24]}... ({ts}) ---")
                    print(f"  Messages: {len(messages)}")
                    for msg in messages:
                        text_single = " ".join(msg["text"].splitlines())
                        truncated = text_single[:300] + "..." if len(text_single) > 300 else text_single
                        print(f"  - {truncated}")

                    total_sessions += 1
                    total_messages += len(messages)

    print(f"\n=== Summary ===")
    print(f"Total sessions: {total_sessions}")
    print(f"Total user messages: {total_messages}")


if __name__ == "__main__":
    main()
