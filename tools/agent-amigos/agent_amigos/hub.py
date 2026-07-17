"""agent-amigos hub — オンプレ専用の薄い中継サーバ（設計書 §5.2、P2）。

git が使えない環境・メッセージ往復のレイテンシを詰めたい環境向けの**任意**コンポーネント。
「追記・所有者上書きのファイル置き場」であり、**調整役ではない**（アサインの勝者決定や
状態遷移は各ノードが決定的に導く — 中央が落ちても壊れない）。

API（stdlib http.server のみ・認証は Bearer トークン・TLS はリバースプロキシに委譲）:
    GET    /ping                            → {"ok": true, "rev": <現在リビジョン>}
    PUT    /o/<relpath>                     → 書き込み（所有者上書き。204 + X-Amigos-Rev）
    GET    /o/<relpath>                     → ファイル内容（404 = 無し）
    GET    /list?prefix=<p>&since=<rev>[&wait=<sec>]
                                            → {"rev": N, "files": [{"path", "rev"}]}
                                              wait 指定時は変化が出るまで long-poll（秒上限）
    DELETE /tree?prefix=<p>                 → サブツリー削除（gc 用）

- データディレクトリはミッションレイアウト（missions/<mid>/…）**そのまま**。
  hub ホスト上の agent-dashboard は busDirs にこのディレクトリを指すだけで読める。
- リビジョンは単調増加の整数（プロセス内カウンタ＋起動時に索引から復元）。
  クライアントは since=rev で差分だけを取る。
- 書き込み競合は所有権分割（設計書 §4.2 — 1 パス 1 書き手）で原理的に起きない前提。
  hub は最後の書き込みを保持するだけで裁定しない。
"""
from __future__ import annotations

import argparse
import json
import os
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

from .util import log, safe_relpath


class HubState:
    """パス → リビジョンの索引。data_dir/.hub-index.json に永続化する。"""

    def __init__(self, data_dir: str):
        self.data_dir = os.path.abspath(data_dir)
        self.lock = threading.Lock()
        self.cond = threading.Condition(self.lock)
        self.index_path = os.path.join(self.data_dir, ".hub-index.json")
        try:
            with open(self.index_path, encoding="utf-8") as f:
                saved = json.load(f)
            self.rev = int(saved.get("rev") or 0)
            self.files = {str(k): int(v) for k, v in dict(saved.get("files") or {}).items()}
        except (OSError, ValueError):
            self.rev = 0
            self.files = {}

    def _persist_locked(self) -> None:
        os.makedirs(self.data_dir, exist_ok=True)
        tmp = f"{self.index_path}.tmp.{os.getpid()}"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump({"rev": self.rev, "files": self.files}, f, ensure_ascii=False)
        os.replace(tmp, self.index_path)

    def put(self, rel: str, body: bytes) -> int:
        path = os.path.join(self.data_dir, rel)
        os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
        tmp = f"{path}.tmp.{os.getpid()}"
        with open(tmp, "wb") as f:
            f.write(body)
        os.replace(tmp, path)
        with self.cond:
            self.rev += 1
            self.files[rel] = self.rev
            self._persist_locked()
            self.cond.notify_all()
            return self.rev

    def delete_tree(self, prefix: str) -> int:
        removed = 0
        with self.cond:
            for rel in [r for r in self.files if r.startswith(prefix)]:
                try:
                    os.remove(os.path.join(self.data_dir, rel))
                except OSError:
                    pass
                del self.files[rel]
                removed += 1
            if removed:
                import shutil
                shutil.rmtree(os.path.join(self.data_dir, prefix.rstrip("/")),
                              ignore_errors=True)     # 空ディレクトリを残さない
                self.rev += 1
                self._persist_locked()
                self.cond.notify_all()
        return removed

    def list_since(self, prefix: str, since: int) -> "tuple[int, list]":
        with self.lock:
            files = [{"path": p, "rev": r} for p, r in sorted(self.files.items())
                     if r > since and p.startswith(prefix)]
            return self.rev, files

    def wait_change(self, since: int, timeout: float) -> None:
        deadline = time.time() + timeout
        with self.cond:
            while self.rev <= since:
                remain = deadline - time.time()
                if remain <= 0:
                    return
                self.cond.wait(min(remain, 5.0))


def make_handler(state: HubState, token: str):
    class Handler(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"

        def log_message(self, fmt, *args):  # noqa: A003 — 標準の 1 行ログを抑制
            pass

        def _auth_ok(self) -> bool:
            if not token:
                return True
            return self.headers.get("Authorization", "") == f"Bearer {token}"

        def _send(self, code: int, body: bytes = b"", ctype: str = "application/json",
                  extra: "dict | None" = None) -> None:
            self.send_response(code)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(body)))
            for k, v in (extra or {}).items():
                self.send_header(k, str(v))
            self.end_headers()
            if body:
                self.wfile.write(body)

        def _json(self, code: int, data) -> None:
            self._send(code, json.dumps(data, ensure_ascii=False).encode("utf-8"))

        def _rel(self) -> "str | None":
            parsed = urlparse(self.path)
            if not parsed.path.startswith("/o/"):
                return None
            try:
                return safe_relpath(parsed.path[3:])
            except ValueError:
                return None

        def do_GET(self):  # noqa: N802
            if not self._auth_ok():
                return self._json(401, {"error": "unauthorized"})
            parsed = urlparse(self.path)
            if parsed.path == "/ping":
                return self._json(200, {"ok": True, "rev": state.rev})
            if parsed.path == "/list":
                q = parse_qs(parsed.query)
                prefix = q.get("prefix", [""])[0]
                since = int(q.get("since", ["0"])[0] or 0)
                wait = min(float(q.get("wait", ["0"])[0] or 0), 55.0)
                if wait > 0:
                    state.wait_change(since, wait)
                rev, files = state.list_since(prefix, since)
                return self._json(200, {"rev": rev, "files": files})
            rel = self._rel()
            if rel is None:
                return self._json(404, {"error": "not found"})
            try:
                with open(os.path.join(state.data_dir, rel), "rb") as f:
                    body = f.read()
            except OSError:
                return self._json(404, {"error": "not found"})
            return self._send(200, body, "application/octet-stream")

        def do_PUT(self):  # noqa: N802
            if not self._auth_ok():
                return self._json(401, {"error": "unauthorized"})
            rel = self._rel()
            if rel is None:
                return self._json(400, {"error": "bad path"})
            length = int(self.headers.get("Content-Length") or 0)
            body = self.rfile.read(length) if length else b""
            rev = state.put(rel, body)
            return self._send(204, extra={"X-Amigos-Rev": rev})

        def do_DELETE(self):  # noqa: N802
            if not self._auth_ok():
                return self._json(401, {"error": "unauthorized"})
            parsed = urlparse(self.path)
            if parsed.path != "/tree":
                return self._json(404, {"error": "not found"})
            prefix = parse_qs(parsed.query).get("prefix", [""])[0]
            try:
                prefix = safe_relpath(prefix) + "/"
            except ValueError:
                return self._json(400, {"error": "bad prefix"})
            removed = state.delete_tree(prefix)
            return self._json(200, {"removed": removed})

    return Handler


def serve(data_dir: str, host: str = "127.0.0.1", port: int = 8765,
          token: "str | None" = None) -> ThreadingHTTPServer:
    """hub サーバを起動して返す（呼び出し側が serve_forever / shutdown を制御する）。"""
    state = HubState(data_dir)
    token = token if token is not None else os.environ.get("AGENT_AMIGOS_HUB_TOKEN", "")
    server = ThreadingHTTPServer((host, port), make_handler(state, token))
    server.hub_state = state  # type: ignore[attr-defined]
    return server


def main_serve(args) -> int:
    server = serve(args.data, args.host, args.port, args.token)
    log("hub", f"agent-amigos hub を起動しました: http://{args.host}:{server.server_port} "
               f"(data={os.path.abspath(args.data)}, auth={'Bearer' if (args.token or os.environ.get('AGENT_AMIGOS_HUB_TOKEN')) else 'なし'})")
    log("hub", "オンプレ限定。インターネット公開は非対応（TLS はリバースプロキシに委譲）")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.shutdown()
    return 0


def add_parser(sub) -> None:
    p = sub.add_parser("hub", help="中継サーバを起動する（オンプレ・任意コンポーネント）")
    p.add_argument("--data", required=True, help="データディレクトリ（ミッションレイアウトそのまま）")
    p.add_argument("--host", default="127.0.0.1")
    p.add_argument("--port", type=int, default=8765)
    p.add_argument("--token", default=None,
                   help="Bearer トークン（既定: 環境変数 AGENT_AMIGOS_HUB_TOKEN）")
    p.set_defaults(fn=main_serve)


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="cmd", required=True)
    add_parser(sub)
    args = ap.parse_args()
    raise SystemExit(args.fn(args))
