"""テスト用の偽の API・偽の dagu・設定の組み立て（標準ライブラリだけ）。"""
from __future__ import annotations

import contextlib
import hashlib
import io
import json
import os
import sys
import tempfile
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
sys.path.insert(0, str(ROOT))

from apiwatch import cli  # noqa: E402
from apiwatch.store import Store  # noqa: E402

TOKEN = "secret-token-123456"
FAKE_DAGU = HERE / "fake_dagu.py"


class FakeAPI:
    """いろいろなページングの口を持つ偽の API。

    /page    ?page=&per_page=           {"data": [...], "total_pages": n}
    /offset  ?offset=&limit=            {"items": [...], "total": n}
    /cursor  ?cursor=  （2 件ずつ）      {"items": [...], "next_cursor": "..."}
    /next    ?p=       （2 件ずつ）      {"results": [...], "next": "/next?p=2"}
    /link    ?page=    （2 件ずつ）      [...]、Link: <...>; rel="next"
    /all                                [...]（ETag を付け、If-None-Match には 304）
    /sync    ?since_rev=                {"items": [...], "sync_token": n}
    どの口も updated_since= があれば updated >= その値のものだけ返す。
    """

    def __init__(self):
        self.items: dict[int, dict] = {}
        self.rev = 0
        self.clock = 100
        self.requests: list[tuple[str, dict, dict]] = []
        self.failures: list[dict] = []
        self.block_page: tuple[str, str] | None = None
        self.blocked = threading.Event()
        self.release = threading.Event()
        self.lock = threading.Lock()
        outer = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *a):
                pass

            def do_GET(self):
                outer._handle(self)

            def do_POST(self):
                outer._handle(self)

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.url = f"http://127.0.0.1:{self.server.server_address[1]}"

    def close(self):
        self.release.set()
        self.server.shutdown()
        self.server.server_close()

    # -- 世界の操作 --
    def put(self, i: int, **fields):
        with self.lock:
            self.rev += 1
            self.clock += 1
            base = dict(self.items.get(i, {"id": i}))
            base.update(fields)
            base["rev"] = self.rev
            base["updated"] = self.clock
            self.items[i] = base

    def drop(self, i: int):
        with self.lock:
            self.items.pop(i, None)

    def fail(self, path: str, status: int, count: int = 1, page: str | None = None, headers: dict | None = None,
             body: str = '{"error":"boom"}'):
        self.failures.append({"path": path, "status": status, "count": count, "page": page,
                              "headers": headers or {}, "body": body})

    def count(self, path: str | None = None) -> int:
        return sum(1 for p, _, _ in self.requests if path is None or p == path)

    # -- 応答 --
    def _send(self, h, status: int, body, headers: dict | None = None):
        raw = b"" if body is None else (body if isinstance(body, bytes) else json.dumps(body).encode())
        h.send_response(status)
        h.send_header("Content-Type", "application/json")
        for k, v in (headers or {}).items():
            h.send_header(k, v)
        h.send_header("Content-Length", str(len(raw)))
        h.end_headers()
        h.wfile.write(raw)

    def _handle(self, h):
        u = urlparse(h.path)
        q = {k: v[-1] for k, v in parse_qs(u.query).items()}
        n = int(h.headers.get("Content-Length") or 0)
        body = h.rfile.read(n) if n else b""
        if body:
            try:
                q.update({k: str(v) for k, v in json.loads(body).items()})
            except ValueError:
                pass
        self.requests.append((u.path, q, dict(h.headers)))
        page = q.get("page") or q.get("offset") or q.get("cursor") or q.get("p")
        for f in self.failures:
            if f["count"] > 0 and f["path"] == u.path and (f["page"] is None or f["page"] == page):
                f["count"] -= 1
                return self._send(h, f["status"], f["body"].encode(), f["headers"])
        if self.block_page and self.block_page == (u.path, page):
            self.blocked.set()
            self.release.wait(30)
        auth = h.headers.get("Authorization")
        if auth != f"Bearer {TOKEN}":
            return self._send(h, 401, {"message": f"bad credentials: {auth}"})
        with self.lock:
            items = sorted(self.items.values(), key=lambda x: x["id"])
        if "updated_since" in q:
            items = [x for x in items if x["updated"] >= int(q["updated_since"])]
        p = u.path
        if p == "/page":
            per = int(q.get("per_page", 2))
            pg = int(q.get("page", 1))
            total_pages = max(1, -(-len(items) // per))
            return self._send(h, 200, {"data": items[(pg - 1) * per: pg * per], "total_pages": total_pages})
        if p == "/offset":
            off, lim = int(q.get("offset", 0)), int(q.get("limit", 2))
            return self._send(h, 200, {"items": items[off: off + lim], "total": len(items)})
        if p == "/cursor":
            start = int(q.get("cursor") or 0)
            nxt = start + 2 if start + 2 < len(items) else None
            return self._send(h, 200, {"items": items[start: start + 2], "next_cursor": None if nxt is None else str(nxt)})
        if p == "/next":
            pg = int(q.get("p", 1))
            nxt = f"/next?p={pg + 1}" if pg * 2 < len(items) else None
            return self._send(h, 200, {"results": items[(pg - 1) * 2: pg * 2], "next": nxt})
        if p == "/link":
            pg = int(q.get("page", 1))
            hdr = {"Link": f'<{self.url}/link?page={pg + 1}>; rel="next"'} if pg * 2 < len(items) else {}
            return self._send(h, 200, items[(pg - 1) * 2: pg * 2], hdr)
        if p == "/all":
            raw = json.dumps(items).encode()
            etag = '"' + hashlib.sha1(raw).hexdigest() + '"'
            if h.headers.get("If-None-Match") == etag:
                return self._send(h, 304, None, {"ETag": etag})
            return self._send(h, 200, raw, {"ETag": etag})
        if p == "/sync":
            since = int(q.get("since_rev") or 0)
            got = [x for x in items if x["rev"] > since]
            return self._send(h, 200, {"items": got, "sync_token": max([since] + [x["rev"] for x in items])})
        return self._send(h, 404, {"message": "not found"})


class FakeDaguAPI:
    """Dagu の REST API（/api/v1）の偽物。enqueue・状態・retry。"""

    def __init__(self, token: str = "dagu-api-token-xyz"):
        self.runs: dict[tuple[str, str], dict] = {}
        self.calls: list[tuple[str, str, dict]] = []
        self.token = token
        outer = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *a):
                pass

            def _send(self, status, body):
                raw = json.dumps(body).encode()
                self.send_response(status)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(raw)))
                self.end_headers()
                self.wfile.write(raw)

            def do_GET(self):
                parts = urlparse(self.path).path.split("/")
                outer.calls.append(("GET", self.path, {}))
                key = (parts[4], parts[5])
                if key not in outer.runs:
                    return self._send(404, {"code": "not_found", "message": "DAGRun not found"})
                return self._send(200, {"dagRunDetails": {"dagRunId": key[1], "statusLabel": outer.runs[key]["status"]}})

            def do_POST(self):
                n = int(self.headers.get("Content-Length") or 0)
                body = json.loads(self.rfile.read(n) or b"{}")
                outer.calls.append(("POST", self.path, body))
                if self.headers.get("Authorization") != f"Bearer {outer.token}":
                    return self._send(401, {"message": "unauthorized"})
                parts = urlparse(self.path).path.split("/")
                if parts[3] == "dags" and parts[5] == "enqueue":
                    key = (parts[4], body["dagRunId"])
                    if key in outer.runs:
                        return self._send(409, {"code": "already_exists",
                                                "message": f"dag-run ID {key[1]} already exists for DAG {key[0]}"})
                    outer.runs[key] = {"status": "queued", "params": json.loads(body["params"])}
                    return self._send(200, {"dagRunId": key[1]})
                if parts[3] == "dag-runs" and parts[6] == "retry":
                    outer.runs[(parts[4], parts[5])]["status"] = "queued"
                    return self._send(200, {})
                return self._send(404, {})

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        threading.Thread(target=self.server.serve_forever, daemon=True).start()
        self.url = f"http://127.0.0.1:{self.server.server_address[1]}"

    def close(self):
        self.server.shutdown()
        self.server.server_close()


class Env:
    """一時フォルダ・偽の API・偽の dagu・設定ファイルをまとめて用意する。"""

    def __init__(self, test):
        self.tmp = Path(tempfile.mkdtemp(prefix="apiwatch-test-"))
        test.addCleanup(self._cleanup)
        self.api = FakeAPI()
        test.addCleanup(self.api.close)
        self.dagu_log = self.tmp / "dagu" / "runs.jsonl"
        self.dagu_log.parent.mkdir()
        self._old_env = {k: os.environ.get(k) for k in ("FAKE_DAGU_LOG",)}
        os.environ["FAKE_DAGU_LOG"] = str(self.dagu_log)
        test.addCleanup(self._restore_env)
        self.conn_file = self.tmp / "connections.json"
        self.conn_file.write_text(json.dumps({"fakeapi": [{"label": "default", "url": self.api.url,
                                                           "token": TOKEN}]}), encoding="utf-8")
        self.config_path = self.tmp / "api-watch.json"

    def _cleanup(self):
        import shutil
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _restore_env(self):
        for k, v in self._old_env.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v

    def write_config(self, sources: dict, rules: dict, **extra) -> Path:
        cfg = {"version": 1, "state_dir": str(self.tmp / "state"),
               "delivery": {"adapter": "dagu", "mode": "cli", "executable": [sys.executable, str(FAKE_DAGU)]},
               "sources": sources, "rules": rules}
        cfg.update(extra)
        self.config_path.write_text(json.dumps(cfg, ensure_ascii=False, indent=2), encoding="utf-8")
        return self.config_path

    def source(self, path: str = "/all", **kw) -> dict:
        d = {"connection": "fakeapi",
             "request": {"url": "{{ conn.url }}" + path,
                         "headers": {"Authorization": "Bearer {{ conn.token }}"}},
             "retry": {"attempts": 3, "backoff_sec": 0},
             "resume_max_age_sec": 3600}
        for k, v in kw.items():
            if k == "query":
                d["request"]["query"] = v
            else:
                d[k] = v
        return d

    def run(self, *args: str) -> tuple[int, str]:
        out = io.StringIO()
        err = io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            code = cli.main(["--config", str(self.config_path), "--connections", str(self.conn_file), *args])
        return code, out.getvalue() + err.getvalue()

    def ok(self, test, *args: str) -> str:
        code, out = self.run(*args)
        test.assertEqual(code, 0, out)
        return out

    def store(self) -> Store:
        return Store(self.tmp / "state")

    def enqueued(self) -> list[dict]:
        if not self.dagu_log.exists():
            return []
        return [json.loads(x) for x in self.dagu_log.read_text(encoding="utf-8").splitlines() if x.strip()]

    def fail_dags(self, *dags: str):
        (self.dagu_log.parent / "fail_dags").write_text("\n".join(dags), encoding="utf-8")

    def deliveries(self) -> list[dict]:
        s = self.store()
        try:
            return [dict(r) for r in s.q("SELECT * FROM deliveries ORDER BY seq, target")]
        finally:
            s.close()


