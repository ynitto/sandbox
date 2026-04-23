#!/usr/bin/env python3
"""
kiro_log_exporter.py - Kiro CLI/IDE セッションログエクスポーター

Kiro のセッション履歴を .log テキストファイルとして指定フォルダへ出力する。
差分更新に対応しており、定期的に繰り返し呼び出すことを想定している。

【対応ソース】
  - kiro-cli : ~/.kiro/store.db (Linux / WSL)
  - kiro-ide : %APPDATA%/Kiro/User/workspaceStorage/ (Windows)
               /mnt/c/Users/*/AppData/Roaming/Kiro/ (WSL 経由)
               \\wsl$\<distro>\home\<user>\.kiro\ (Windows ネイティブ経由)

【使い方】
  python kiro_log_exporter.py <出力フォルダ>
  python kiro_log_exporter.py <出力フォルダ> --source cli
  python kiro_log_exporter.py <出力フォルダ> --source ide
  python kiro_log_exporter.py <出力フォルダ> --source all   # デフォルト
  python kiro_log_exporter.py <出力フォルダ> --kiro-db ~/.kiro/store.db -v
"""

import argparse
import json
import os
import re
import sqlite3
import subprocess
import sys
from datetime import datetime
from pathlib import Path
from typing import Optional


# 差分管理ファイル名（出力フォルダ直下に配置）
_STATE_FILE = ".kiro_export_state.json"


# ── OS / 環境検出 ─────────────────────────────────────────────────────────────

def _is_wsl() -> bool:
    if sys.platform != "linux":
        return False
    try:
        return "microsoft" in Path("/proc/version").read_text(errors="ignore").lower()
    except OSError:
        return False


def _is_windows() -> bool:
    return sys.platform == "win32"


# ── パス解決 ──────────────────────────────────────────────────────────────────

def _kiro_cli_db_candidates() -> list[Path]:
    """kiro-cli SQLite DB の候補パスを優先順で返す。"""
    base = Path.home() / ".kiro"
    return [
        base / "store.db",
        base / "sessions.db",
        base / "db" / "sessions.db",
        base / "data" / "sessions.db",
    ]


def _kiro_cli_db_wsl_candidates() -> list[Path]:
    """Windows ネイティブから WSL 内の kiro-cli DB へアクセスするためのパスを返す。

    \\wsl$\<distro>\home\<user>\.kiro\store.db 形式の UNC パスを生成する。
    """
    if not _is_windows():
        return []

    candidates: list[Path] = []
    distros = ["Ubuntu", "Ubuntu-22.04", "Ubuntu-24.04", "Ubuntu-20.04", "Debian", "kali-linux"]
    for distro in distros:
        try:
            result = subprocess.run(
                ["wsl", "-d", distro, "-e", "bash", "-c", "echo $HOME"],
                capture_output=True, text=True, timeout=10,
            )
            if result.returncode != 0:
                continue
            wsl_home = result.stdout.strip()
            # WSL UNC パスに変換: /home/user → \\wsl$\Ubuntu\home\user
            rel = wsl_home.lstrip("/").replace("/", "\\")
            base_unc = Path(f"\\\\wsl$\\{distro}") / rel / ".kiro"
            for db_name in ("store.db", "sessions.db"):
                candidates.append(base_unc / db_name)
        except (FileNotFoundError, subprocess.TimeoutExpired):
            continue
    return candidates


def _kiro_ide_storage_paths() -> list[Path]:
    """kiro-ide workspaceStorage のパスを OS に応じて返す。

    複数パスを返す可能性がある（例: WSL 上で Windows パスも検索する場合）。
    """
    paths: list[Path] = []

    if _is_windows():
        # Windows ネイティブ
        appdata = os.environ.get("APPDATA", "")
        if appdata:
            p = Path(appdata) / "Kiro" / "User" / "workspaceStorage"
            if p.exists():
                paths.append(p)

    elif _is_wsl():
        # WSL: Linux ネイティブパス（Linux 版 Kiro IDE が存在すれば）
        linux_p = Path.home() / ".config" / "Kiro" / "User" / "workspaceStorage"
        if linux_p.exists():
            paths.append(linux_p)
        # WSL → Windows の Kiro IDE パス
        paths.extend(_kiro_ide_wsl_windows_paths())

    elif sys.platform == "darwin":
        p = Path.home() / "Library" / "Application Support" / "Kiro" / "User" / "workspaceStorage"
        if p.exists():
            paths.append(p)

    else:
        # Linux ネイティブ
        p = Path.home() / ".config" / "Kiro" / "User" / "workspaceStorage"
        if p.exists():
            paths.append(p)

    return paths


def _kiro_ide_wsl_windows_paths() -> list[Path]:
    """WSL 環境から Windows 側の Kiro IDE workspaceStorage パスを探索する。"""
    try:
        result = subprocess.run(
            ["wslpath", "-u", r"C:\Users"],
            capture_output=True, text=True, timeout=5,
        )
        if result.returncode != 0:
            return []
        win_users = Path(result.stdout.strip())
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return []

    found: list[Path] = []
    try:
        for user_dir in win_users.iterdir():
            candidate = user_dir / "AppData" / "Roaming" / "Kiro" / "User" / "workspaceStorage"
            if candidate.exists():
                found.append(candidate)
    except (PermissionError, OSError):
        pass
    return found


# ── 差分管理 ──────────────────────────────────────────────────────────────────

class _ExportState:
    """出力済みセッションの状態を管理し、差分更新を実現する。

    状態はJSON形式で出力フォルダの .kiro_export_state.json に保存される。

    スキーマ:
      {
        "sessions": {
          "<session_key>": {
            "updated_at": <float>,      // 最後にエクスポートした時点の updated_at
            "output_file": "<filename>" // 出力済み .log ファイル名
          }
        }
      }
    """

    def __init__(self, state_path: Path) -> None:
        self._path = state_path
        self._data: dict = self._load()

    def _load(self) -> dict:
        if self._path.exists():
            try:
                return json.loads(self._path.read_text(encoding="utf-8"))
            except (json.JSONDecodeError, OSError):
                pass
        return {"sessions": {}}

    def save(self) -> None:
        self._path.write_text(
            json.dumps(self._data, indent=2, ensure_ascii=False),
            encoding="utf-8",
        )

    def last_updated_at(self, key: str) -> float:
        return float(self._data["sessions"].get(key, {}).get("updated_at", 0.0))

    def saved_output_file(self, key: str) -> Optional[str]:
        return self._data["sessions"].get(key, {}).get("output_file")

    def record(self, key: str, updated_at: float, filename: str) -> None:
        self._data["sessions"][key] = {"updated_at": updated_at, "output_file": filename}


# ── kiro-cli セッション読み込み ───────────────────────────────────────────────

def _read_cli_sessions(db_path: Path) -> list[dict]:
    """kiro-cli の SQLite DB からセッション一覧を読み込む。"""
    if not db_path.exists():
        return []

    sessions: list[dict] = []
    try:
        conn = sqlite3.connect(str(db_path))
        cur = conn.cursor()

        cur.execute("SELECT name FROM sqlite_master WHERE type='table'")
        tables = {r[0].lower() for r in cur.fetchall()}

        table = next(
            (t for t in ("sessions", "chat_sessions", "conversations") if t in tables),
            None,
        )
        if table is None:
            conn.close()
            return []

        cur.execute(f"SELECT * FROM [{table}]")  # noqa: S608
        cols = [d[0].lower() for d in cur.description]

        for row in cur.fetchall():
            data = dict(zip(cols, row))
            s = _parse_cli_row(data, db_path)
            if s:
                sessions.append(s)

        conn.close()
    except (sqlite3.Error, OSError):
        pass

    return sessions


def _parse_cli_row(data: dict, db_path: Path) -> Optional[dict]:
    sid = str(data.get("id") or "").strip()
    if not sid:
        return None

    directory = (
        data.get("directory")
        or data.get("project_path")
        or data.get("workspace")
        or ""
    )
    created_at = _to_sec(data.get("created_at", 0))
    updated_at = _to_sec(data.get("updated_at") or data.get("created_at", 0))
    messages = _extract_messages_from_row(data)

    return {
        "session_id": sid,
        "source_type": "kiro-cli",
        "source_db": str(db_path),
        "directory": str(directory),
        "created_at": created_at,
        "updated_at": updated_at,
        "messages": messages,
    }


def _extract_messages_from_row(data: dict) -> list[dict]:
    """セッション行データから user / assistant 両方のメッセージを抽出する。"""
    raw = None
    for key in ("messages", "conversation", "content", "history", "data"):
        val = data.get(key)
        if val:
            raw = val
            break

    if raw is None:
        return []

    if isinstance(raw, str):
        try:
            raw = json.loads(raw)
        except (json.JSONDecodeError, ValueError):
            return []

    if not isinstance(raw, list):
        return []

    result: list[dict] = []
    for item in raw:
        if not isinstance(item, dict):
            continue
        role_raw = item.get("role", "").lower()
        if role_raw in ("user", "human"):
            role = "User"
        elif role_raw == "assistant":
            role = "Assistant"
        else:
            continue

        content = item.get("content") or item.get("text") or item.get("message") or ""
        if isinstance(content, list):
            text = "\n".join(
                b.get("text", "")
                for b in content
                if isinstance(b, dict) and b.get("type") == "text"
            )
        else:
            text = str(content)

        text = text.strip()
        if not text:
            continue

        ts = _to_sec(item.get("timestamp") or item.get("created_at") or 0)
        result.append({"role": role, "text": text, "timestamp": ts})

    return result


# ── kiro-ide セッション読み込み ───────────────────────────────────────────────

def _read_ide_sessions(storage_path: Path) -> list[dict]:
    """Kiro IDE の workspaceStorage からセッション一覧を読み込む。"""
    if not storage_path.exists():
        return []

    sessions: list[dict] = []
    try:
        for ws_dir in sorted(storage_path.iterdir()):
            if not ws_dir.is_dir():
                continue

            ws_name = _get_ws_name(ws_dir)
            chat_dir = ws_dir / "chatSessions"

            if chat_dir.is_dir():
                for f in sorted(chat_dir.glob("*.json")):
                    s = _parse_ide_json(f, ws_name, storage_path)
                    if s:
                        sessions.append(s)
            else:
                # フォールバック: state.vscdb
                vscdb = ws_dir / "state.vscdb"
                if vscdb.exists():
                    sessions.extend(_read_ide_vscdb(vscdb, ws_name, storage_path))

    except (OSError, PermissionError):
        pass

    return sessions


def _get_ws_name(ws_dir: Path) -> str:
    ws_json = ws_dir / "workspace.json"
    if ws_json.exists():
        try:
            data = json.loads(ws_json.read_text(encoding="utf-8"))
            folder = data.get("folder", "")
            if folder:
                return Path(folder.replace("file:///", "").replace("file://", "")).name
        except (json.JSONDecodeError, OSError):
            pass
    return ws_dir.name[:8]


def _parse_ide_json(session_file: Path, ws_name: str, storage_path: Path) -> Optional[dict]:
    try:
        data = json.loads(session_file.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return None

    requests = data.get("requests", [])
    if not requests:
        return None

    messages: list[dict] = []
    timestamps: list[float] = []

    for req in requests:
        ts = _to_sec(req.get("timestamp", 0))
        timestamps.append(ts)

        msg = req.get("message", {})
        msg_text = msg.get("text", "") if isinstance(msg, dict) else str(msg or "")
        if msg_text.strip():
            messages.append({"role": "User", "text": msg_text.strip(), "timestamp": ts})

        resp = req.get("response", {})
        if isinstance(resp, dict):
            resp_text = resp.get("value") or resp.get("text") or ""
        else:
            resp_text = str(resp) if resp else ""
        resp_ts = _to_sec(req.get("responseTimestamp", ts))
        if resp_text.strip():
            messages.append({"role": "Assistant", "text": resp_text.strip(), "timestamp": resp_ts})

    if not messages:
        return None

    return {
        "session_id": session_file.stem,
        "source_type": "kiro-ide",
        "source_db": str(storage_path),
        "directory": ws_name,
        "created_at": min(timestamps) if timestamps else 0.0,
        "updated_at": max(timestamps) if timestamps else 0.0,
        "messages": messages,
    }


def _read_ide_vscdb(db_path: Path, ws_name: str, storage_path: Path) -> list[dict]:
    """state.vscdb から chatSessions キーを探してセッションを読む。"""
    sessions: list[dict] = []
    try:
        conn = sqlite3.connect(str(db_path))
        cur = conn.cursor()
        cur.execute(
            "SELECT value FROM ItemTable WHERE key LIKE '%chatSessions%'"
        )
        for (value,) in cur.fetchall():
            if not value:
                continue
            try:
                raw = json.loads(value)
            except (json.JSONDecodeError, ValueError):
                continue

            items = raw if isinstance(raw, list) else raw.get("sessions", [raw])
            for item in items:
                if not isinstance(item, dict):
                    continue
                reqs = item.get("requests", [])
                if not reqs:
                    continue

                messages: list[dict] = []
                timestamps: list[float] = []
                for req in reqs:
                    ts = _to_sec(req.get("timestamp", 0))
                    timestamps.append(ts)
                    msg = req.get("message", {})
                    msg_text = msg.get("text", "") if isinstance(msg, dict) else ""
                    if msg_text.strip():
                        messages.append({"role": "User", "text": msg_text.strip(), "timestamp": ts})
                    resp = req.get("response", {})
                    resp_text = resp.get("value", "") if isinstance(resp, dict) else ""
                    if resp_text.strip():
                        messages.append({"role": "Assistant", "text": resp_text.strip(), "timestamp": ts})

                if messages:
                    sessions.append({
                        "session_id": item.get("sessionId", str(abs(hash(str(item))))[:12]),
                        "source_type": "kiro-ide",
                        "source_db": str(storage_path),
                        "directory": ws_name,
                        "created_at": min(timestamps) if timestamps else 0.0,
                        "updated_at": max(timestamps) if timestamps else 0.0,
                        "messages": messages,
                    })
        conn.close()
    except (sqlite3.Error, OSError):
        pass
    return sessions


# ── ユーティリティ ────────────────────────────────────────────────────────────

def _to_sec(ts) -> float:
    """タイムスタンプを Unix 秒（float）へ正規化する。ミリ秒も自動判定。"""
    if not ts:
        return 0.0
    try:
        v = float(ts)
    except (ValueError, TypeError):
        return 0.0
    return v / 1000.0 if v > 1e10 else v


def _fmt_ts(ts: float) -> str:
    if not ts:
        return "----"
    try:
        return datetime.fromtimestamp(ts).strftime("%Y-%m-%d %H:%M:%S")
    except (OSError, OverflowError, ValueError):
        return "----"


def _safe_name(s: str, max_len: int = 24) -> str:
    """ファイル名に使える文字のみ残し、長さを制限する。"""
    return re.sub(r"[^\w\-]", "_", s)[:max_len].strip("_")


def _make_filename(session: dict) -> str:
    ts = session.get("created_at") or session.get("updated_at") or 0.0
    date_str = datetime.fromtimestamp(ts).strftime("%Y%m%d_%H%M%S") if ts else "00000000_000000"
    src = session["source_type"].replace("-", "_")
    ws = _safe_name(Path(session.get("directory", "unknown")).name)
    sid = re.sub(r"[^a-zA-Z0-9]", "", session["session_id"])[:12]
    return f"{date_str}_{src}_{ws}_{sid}.log"


def _session_key(s: dict) -> str:
    return f"{s['source_type']}::{s['source_db']}::{s['session_id']}"


# ── ログフォーマット ──────────────────────────────────────────────────────────

def _format_log(session: dict) -> str:
    SEP = "=" * 80
    lines = [
        SEP,
        f"Session:  {session['session_id']}",
        f"Source:   {session['source_type']}",
        f"Path:     {session.get('directory', '')}",
        f"Created:  {_fmt_ts(session.get('created_at', 0))}",
        f"Updated:  {_fmt_ts(session.get('updated_at', 0))}",
        SEP,
        "",
    ]

    for msg in session.get("messages", []):
        ts_str = _fmt_ts(msg.get("timestamp", 0))
        lines.append(f"[{ts_str}] {msg['role']}")
        lines.append(msg["text"])
        lines.append("")

    lines.append(SEP)
    return "\n".join(lines)


# ── エクスポートメイン ────────────────────────────────────────────────────────

def _export_sessions(
    sessions: list[dict],
    output_dir: Path,
    state: _ExportState,
    verbose: bool,
) -> tuple[int, int]:
    """セッションを .log ファイルへ差分エクスポートする。

    戻り値: (新規件数, 更新件数)
    """
    new_cnt = updated_cnt = 0

    for s in sessions:
        key = _session_key(s)
        updated_at = s.get("updated_at", 0.0)
        last = state.last_updated_at(key)

        if updated_at > 0 and updated_at <= last:
            if verbose:
                print(f"  skip (no change): {s['session_id'][:16]}…")
            continue

        is_new = last == 0.0
        existing = state.saved_output_file(key)
        filename = existing if existing else _make_filename(s)
        out_path = output_dir / filename

        try:
            out_path.write_text(_format_log(s), encoding="utf-8")
        except OSError as e:
            print(f"  [warn] write failed: {out_path}: {e}", file=sys.stderr)
            continue

        state.record(key, updated_at, filename)

        if is_new:
            new_cnt += 1
            if verbose:
                print(f"  new: {filename}")
        else:
            updated_cnt += 1
            if verbose:
                print(f"  updated: {filename}")

    return new_cnt, updated_cnt


# ── CLI エントリポイント ──────────────────────────────────────────────────────

def _build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="kiro_log_exporter",
        description="Kiro CLI/IDE セッションログを .log ファイルへエクスポートする（差分更新対応）",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
使用例:
  python kiro_log_exporter.py ~/kiro-logs
  python kiro_log_exporter.py ~/kiro-logs --source cli
  python kiro_log_exporter.py ~/kiro-logs --source ide
  python kiro_log_exporter.py ~/kiro-logs --kiro-db ~/.kiro/store.db -v

Windows (WSL + Windows OS 両方を取得):
  python kiro_log_exporter.py C:\\kiro-logs --source all

差分管理ファイル: <出力フォルダ>/.kiro_export_state.json
""".strip(),
    )
    p.add_argument("output_dir", help="ログファイルの出力先フォルダ")
    p.add_argument(
        "--source",
        choices=["cli", "ide", "all"],
        default="all",
        help="取得元: cli=kiro-cli のみ / ide=kiro-ide のみ / all=両方 (デフォルト: all)",
    )
    p.add_argument(
        "--kiro-db",
        metavar="PATH",
        help="kiro-cli SQLite DB のカスタムパス（省略時は自動検出）",
    )
    p.add_argument(
        "-v", "--verbose",
        action="store_true",
        help="詳細ログを表示する",
    )
    return p


def main() -> None:
    args = _build_parser().parse_args()

    output_dir = Path(args.output_dir).expanduser().resolve()
    output_dir.mkdir(parents=True, exist_ok=True)

    state = _ExportState(output_dir / _STATE_FILE)
    all_sessions: list[dict] = []

    include_cli = args.source in ("cli", "all")
    include_ide = args.source in ("ide", "all")

    # ── kiro-cli ──
    if include_cli:
        if args.kiro_db:
            db_list = [Path(args.kiro_db).expanduser()]
        else:
            db_list = _kiro_cli_db_candidates()
            if _is_windows():
                db_list.extend(_kiro_cli_db_wsl_candidates())

        cli_loaded = False
        for db_path in db_list:
            if db_path.exists():
                sessions = _read_cli_sessions(db_path)
                if verbose := args.verbose:
                    print(f"[kiro-cli] {len(sessions)} sessions <- {db_path}")
                all_sessions.extend(sessions)
                cli_loaded = True
                break  # 最初に見つかった DB を使用

        if not cli_loaded and args.verbose:
            print("[kiro-cli] DB が見つかりません")

    # ── kiro-ide ──
    if include_ide:
        for storage_path in _kiro_ide_storage_paths():
            sessions = _read_ide_sessions(storage_path)
            if args.verbose:
                print(f"[kiro-ide] {len(sessions)} sessions <- {storage_path}")
            all_sessions.extend(sessions)

        if not _kiro_ide_storage_paths() and args.verbose:
            print("[kiro-ide] workspaceStorage が見つかりません")

    if not all_sessions:
        print("セッションが見つかりませんでした。")
        return

    new_cnt, upd_cnt = _export_sessions(all_sessions, output_dir, state, args.verbose)
    state.save()

    total = len(all_sessions)
    skip_cnt = total - new_cnt - upd_cnt
    print(
        f"完了: {total} セッション検出 → 新規 {new_cnt} 件, 更新 {upd_cnt} 件, "
        f"スキップ {skip_cnt} 件  [{output_dir}]"
    )


if __name__ == "__main__":
    main()
