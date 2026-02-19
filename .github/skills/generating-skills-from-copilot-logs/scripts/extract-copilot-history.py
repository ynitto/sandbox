#!/usr/bin/env python3
"""
VSCode Copilot Chat History Extractor

VSCode の workspaceStorage から Copilot チャット履歴を取得・フィルタリングし、
スキル生成のためのパターン分析を支援する。

使用例:
  python extract-copilot-history.py --days 90 --noise-filter
  python extract-copilot-history.py --workspace "/path/to/project" --days 30
  python extract-copilot-history.py --storage /custom/path/workspaceStorage
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
        description="VSCode Copilot チャット履歴をスキル生成用に抽出・フィルタリングする"
    )
    parser.add_argument("--workspace", help="ワークスペースパスのサブ文字列でフィルタ")
    parser.add_argument(
        "--days", type=int, default=90,
        help="過去 N 日分のみ対象にする（デフォルト: 90）"
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
    args = parser.parse_args()

    storage_path = Path(args.storage) if args.storage else get_vscode_storage_path()

    if not storage_path.exists():
        print(f"[ERROR] workspaceStorage が見つかりません: {storage_path}", file=sys.stderr)
        print(
            "  --storage オプションでパスを指定するか、"
            "VSCode がインストール済みか確認してください。",
            file=sys.stderr,
        )
        sys.exit(1)

    cutoff = (
        (datetime.now() - timedelta(days=args.days)).timestamp()
        if args.days else 0
    )

    workspace_dirs = [d for d in storage_path.iterdir() if d.is_dir()]

    total_sessions = 0
    total_messages = 0

    for ws_dir in workspace_dirs:
        ws_name = get_workspace_name(ws_dir)

        # ワークスペースフィルタ
        if args.workspace and args.workspace.lower() not in ws_name.lower():
            continue

        # chatSessions → state.vscdb の順で試みる
        sessions = load_from_chat_sessions(ws_dir)
        if not sessions:
            sessions = load_from_state_db(ws_dir)

        if not sessions:
            continue

        # 日付フィルタ & ソート（新しい順）
        if cutoff:
            sessions = [s for s in sessions if s["mtime"] >= cutoff]
        sessions = sorted(sessions, key=lambda s: s["mtime"], reverse=True)[: args.max_sessions]

        if not sessions:
            continue

        print(f"\n=== Workspace: {ws_name} ===")
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
                text = msg["text"]
                # 改行を空白に変換して1行で表示
                text_single = " ".join(text.splitlines())
                truncated = text_single[:300] + "..." if len(text_single) > 300 else text_single
                print(f"  - {truncated}")

            ws_messages += len(messages)
            total_sessions += 1

        total_messages += ws_messages

    print(f"\n=== Summary ===")
    print(f"Total sessions: {total_sessions}")
    print(f"Total user messages: {total_messages}")


if __name__ == "__main__":
    main()
