#!/usr/bin/env python3
"""gitlab-issue-watch.py — GitLab イシューをポーリングし、新規/更新分を
Hermes gateway の webhook route（loopback）へ GitLab Issue Hook 互換の
ペイロードとして投函するスクリプト。

hermes cron の `--no-agent --script` から定期実行されることを想定:

  - 新着なし → 何も出力せず終了（hermes cron は空出力をサイレント配信扱い）
  - 新着あり → route へ POST し、結果を 1 行ずつ stdout に出力

検知ロジックは tools/kiro-loop/hooks/gitlab-issue-hook.py と同じ
「iid -> updated_at の状態ファイル比較」方式。GitLab API 呼び出しは
gitlab-idd スキル同梱の scripts/gl.py に委譲する（依存: stdlib のみ）。

ペイロードは実物の GitLab Issue Hook と同じく `object_attributes` 下に
イシューを置くため、Hermes 側の route 設定は将来 GitLab webhook 直結
（検討メモの案A）へ移行してもそのまま使い回せる。
ただし `labels` は gl.py（REST API）の出力どおり文字列配列であり、
実物 webhook のオブジェクト配列とは異なる点に注意。

使い方:
  python gitlab-issue-watch.py            # 検知して投函（通常運用）
  python gitlab-issue-watch.py --init     # 現状を既読として記録（投函しない）
  python gitlab-issue-watch.py --dry-run  # 投函対象の表示のみ
"""

import argparse
import http.client
import json
import os
import socket
import subprocess
import sys
from pathlib import Path
from urllib.parse import urlparse

# --- 設定（環境変数で上書き可能）---------------------------------------
# gl.py のパス（gitlab-idd スキル同梱）。
GL_PY = os.environ.get("HERMES_GLGW_GL_PY", "scripts/gl.py")
# gl.py を実行する作業ディレクトリ（git remote / connections.yaml の解決用）。
WORKDIR = os.environ.get("HERMES_GLGW_GL_CWD") or None
# Python インタプリタ。
PYTHON = os.environ.get("HERMES_GLGW_PYTHON", sys.executable or "python3")
# gl.py サブプロセスのタイムアウト（秒）。
GL_TIMEOUT = int(os.environ.get("HERMES_GLGW_GL_TIMEOUT", "20"))

# --- フィルター条件 ------------------------------------------------------
ISSUE_STATE = os.environ.get("HERMES_GLGW_ISSUE_STATE", "opened")
# 例: "status::todo"。claim 運用（着手時に status::doing へ付け替え）と
# 組み合わせることで、エージェント自身の更新による再トリガーを防ぐ。
ISSUE_LABELS = os.environ.get("HERMES_GLGW_ISSUE_LABELS", "")
ISSUE_EXCLUDE_LABELS = os.environ.get("HERMES_GLGW_ISSUE_EXCLUDE_LABELS", "")
ISSUE_ASSIGNEE = os.environ.get("HERMES_GLGW_ISSUE_ASSIGNEE", "")

# --- 投函先（Hermes webhook route）--------------------------------------
WEBHOOK_URL = os.environ.get(
    "HERMES_GLGW_WEBHOOK_URL", "http://127.0.0.1:8644/webhooks/gitlab-issues"
)
# route の secret（X-Gitlab-Token として送る）。空なら送らない。
SECRET = os.environ.get("HERMES_GLGW_SECRET", "")
# 接続タイムアウト（秒）。接続失敗 = 未配達なので次サイクルで再送される。
CONNECT_TIMEOUT = int(os.environ.get("HERMES_GLGW_CONNECT_TIMEOUT", "10"))
# 応答待ちタイムアウト（秒）。Hermes の agent mode はエージェント完走後に
# 200 を返す同期型のため、ここを超えても「送達済み」として扱う。
POST_TIMEOUT = int(os.environ.get("HERMES_GLGW_POST_TIMEOUT", "30"))
# 1 サイクルで投函する最大件数（初回フラッディング・暴走の保険）。
MAX_POSTS = int(os.environ.get("HERMES_GLGW_MAX_POSTS", "3"))

# 状態ファイル（iid -> updated_at を記録）。
STATE_FILE = Path(
    os.environ.get("HERMES_GLGW_STATE_FILE", "")
    or (Path.home() / ".hermes" / "gitlab-issue-gateway" / "state.json")
)


def _run_gl(*gl_args: str):
    """gl.py を実行して JSON をパースして返す。失敗時は None。"""
    cmd = [PYTHON, GL_PY, *gl_args]
    try:
        r = subprocess.run(
            cmd, capture_output=True, text=True, timeout=GL_TIMEOUT, cwd=WORKDIR
        )
    except (subprocess.TimeoutExpired, OSError) as e:
        print(f"[glgw] gl.py 実行エラー: {e}", file=sys.stderr)
        return None
    if r.returncode != 0:
        print(f"[glgw] gl.py 失敗 (rc={r.returncode}): {r.stderr.strip()[:300]}",
              file=sys.stderr)
        return None
    try:
        return json.loads(r.stdout)
    except json.JSONDecodeError:
        print("[glgw] gl.py の出力が JSON ではありません", file=sys.stderr)
        return None


def _get_issues() -> "list[dict] | None":
    args = ["list-issues", "--state", ISSUE_STATE]
    if ISSUE_LABELS:
        args += ["--label", ISSUE_LABELS]
    if ISSUE_EXCLUDE_LABELS:
        args += ["--exclude-labels", ISSUE_EXCLUDE_LABELS]
    if ISSUE_ASSIGNEE:
        args += ["--assignee", ISSUE_ASSIGNEE]
    data = _run_gl(*args)
    return data if isinstance(data, list) else None


def _load_state() -> "dict[str, str]":
    try:
        raw = json.loads(STATE_FILE.read_text(encoding="utf-8"))
        return {str(k): str(v) for k, v in raw.get("issues", {}).items()}
    except Exception:
        return {}


def _save_state(state: "dict[str, str]") -> None:
    STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
    tmp = STATE_FILE.with_suffix(".tmp")
    tmp.write_text(
        json.dumps({"issues": state}, ensure_ascii=False), encoding="utf-8"
    )
    tmp.replace(STATE_FILE)


def _post(issue: dict, action: str) -> "tuple[bool, str]":
    """Issue Hook 互換ペイロードを route へ POST する。

    戻り値: (送達済みか, 表示用ステータス)。
    「送達済み」= リクエスト送信完了以降（応答待ちタイムアウト含む）。
    """
    payload = {
        "object_kind": "issue",
        "event_type": "issue",
        "object_attributes": {**issue, "action": action},
    }
    body = json.dumps(payload, ensure_ascii=False).encode("utf-8")

    u = urlparse(WEBHOOK_URL)
    conn_cls = http.client.HTTPSConnection if u.scheme == "https" else http.client.HTTPConnection
    conn = conn_cls(u.hostname, u.port, timeout=CONNECT_TIMEOUT)
    headers = {
        "Content-Type": "application/json",
        "X-Gitlab-Event": "Issue Hook",
        # Hermes 側の冪等性ガード用（同一イシュー・同一 updated_at は重複扱い）
        "X-Request-ID": f"glgw-{issue.get('iid')}-{issue.get('updated_at', '')}",
    }
    if SECRET:
        headers["X-Gitlab-Token"] = SECRET

    try:
        conn.connect()
    except OSError as e:
        return False, f"接続失敗: {e}"

    try:
        conn.request("POST", u.path or "/", body=body, headers=headers)
        # リクエストは送り切った。以降は応答待ちのみ（agent mode は同期で
        # 完走まで返さないため、タイムアウトしても送達済みとみなす）。
        conn.sock.settimeout(POST_TIMEOUT)
        try:
            resp = conn.getresponse()
            detail = resp.read(200).decode("utf-8", errors="replace").strip()
            if 200 <= resp.status < 300:
                return True, f"{resp.status} {detail}"
            # 401/404 等は設定ミス。未送達扱いにして次サイクルで再送する。
            return False, f"{resp.status} {detail}"
        except (TimeoutError, socket.timeout):
            return True, f"送信済み（応答待ち {POST_TIMEOUT}s 超過、エージェント実行中）"
    except OSError as e:
        return False, f"送信エラー: {e}"
    finally:
        conn.close()


def check(*, dry_run: bool = False, init: bool = False) -> int:
    issues = _get_issues()
    if issues is None:
        print("[glgw] イシュー一覧の取得に失敗しました（state は変更しません）")
        return 1

    prev = _load_state()
    curr = {str(i["iid"]): str(i.get("updated_at", "")) for i in issues if "iid" in i}

    if init:
        _save_state(curr)
        print(f"[glgw] 初期化: {len(curr)} 件を既読として記録しました（投函なし）")
        return 0

    changed = [
        i for i in issues
        if "iid" in i and prev.get(str(i["iid"])) != str(i.get("updated_at", ""))
    ]
    if not changed:
        # フィルターから外れたイシューの掃除だけして黙って終了。
        if curr != prev:
            _save_state(curr)
        return 0

    changed.sort(key=lambda i: str(i.get("updated_at", "")))  # 古い順に処理
    overflow = len(changed) - MAX_POSTS
    changed = changed[:MAX_POSTS]

    # 状態は「投函に成功したものだけ」更新し、失敗分は次サイクルで再送する。
    new_state = {
        iid: ts for iid, ts in curr.items()
        if prev.get(iid) == ts  # 未変更分はそのまま既読
    }
    rc = 0
    for issue in changed:
        iid = str(issue["iid"])
        action = "open" if iid not in prev else "update"
        title = str(issue.get("title", ""))[:60]
        if dry_run:
            print(f"[glgw] (dry-run) #{iid} [{action}] {title}")
            continue
        delivered, status = _post(issue, action)
        if delivered:
            new_state[iid] = str(issue.get("updated_at", ""))
            print(f"[glgw] #{iid} [{action}] {title} → 投函 {status}")
        else:
            rc = 1
            print(f"[glgw] #{iid} [{action}] {title} → 失敗 {status}（次サイクルで再送）")

    if not dry_run:
        _save_state(new_state)
    if overflow > 0:
        print(f"[glgw] 残り {overflow} 件は次サイクルで投函します（MAX_POSTS={MAX_POSTS}）")
    return rc


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    g = ap.add_mutually_exclusive_group()
    g.add_argument("--init", action="store_true",
                   help="現在のイシューを既読として記録するだけ（初回セットアップ用）")
    g.add_argument("--dry-run", action="store_true",
                   help="投函対象を表示するだけで POST しない")
    args = ap.parse_args()
    return check(dry_run=args.dry_run, init=args.init)


if __name__ == "__main__":
    sys.exit(main())
