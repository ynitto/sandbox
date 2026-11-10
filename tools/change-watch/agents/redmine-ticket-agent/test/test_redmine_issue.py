"""redmine-ticket-agent のテスト（標準ライブラリと PyYAML。`python -m unittest discover -s test`）。

設定の例（change-watch.yaml.example）と Dagu のワークフローの例を、本物の change-watch で Dagu と同じ順に回したときの流れを
確かめる。Redmine は手元の HTTP サーバで偽装する。
"""
from __future__ import annotations

import json
import os
import sys
import tempfile
import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

try:
    import yaml
except ImportError:      # pragma: no cover
    yaml = None

HERE = Path(__file__).resolve().parent
CHANGE_WATCH = HERE.parents[2] / "change_watch.py"   # tools/change-watch/agents/<エージェント>/test

KEY = "k3y"
ME = {"id": 5, "name": "自分"}
ALICE = {"id": 9, "name": "alice"}
STATUSES = [{"id": 1, "name": "新規", "is_closed": False}, {"id": 2, "name": "進行中", "is_closed": False},
            {"id": 5, "name": "完了", "is_closed": True}, {"id": 6, "name": "却下", "is_closed": True}]
CLOSED = {s["id"] for s in STATUSES if s["is_closed"]}


def make_issue(iid: int, subject: str = "", description: str = "", assignee=ME, status: int = 1) -> dict:
    st = next(s for s in STATUSES if s["id"] == status)
    return {"id": iid, "project": {"id": 1, "name": "p"}, "subject": subject or f"チケット {iid}",
            "description": description, "status": dict(st), "assigned_to": dict(assignee) if assignee else None,
            "updated_on": "2026-11-01T00:00:00Z"}


class FakeRedmine:
    """/issues.json・/issues/N.json（GET・PUT）・/users/current.json・/issue_statuses.json。"""

    def __init__(self):
        self.issues: dict[int, dict] = {}
        self.notes: dict[int, list[str]] = {}
        self.allowed: dict[int, list[int]] = {}   # チケットごとに選べるステータス（無ければすべて）
        self.puts: list[tuple[int, dict]] = []
        self.legacy = False                        # Redmine 5.1 より前: チケットのステータスに is_closed が無い
        self.lock = threading.Lock()
        outer = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *a):
                pass

            def _auth(self) -> bool:
                if self.headers.get("X-Redmine-API-Key") != KEY:
                    self._send(401, {"errors": ["401"]})
                    return False
                return True

            def do_GET(self):
                if not self._auth():
                    return
                u = urlparse(self.path)
                q = parse_qs(u.query)
                with outer.lock:
                    issues = json.loads(json.dumps(outer.issues))
                if outer.legacy:
                    for it in issues.values():
                        it["status"].pop("is_closed", None)
                if u.path == "/users/current.json":
                    return self._send(200, {"user": {"id": ME["id"], "login": "me"}})
                if u.path == "/issue_statuses.json":
                    return self._send(200, {"issue_statuses": STATUSES})
                if u.path == "/issues.json":
                    items = sorted(issues.values(), key=lambda i: i["id"])
                    if q.get("assigned_to_id") == ["me"]:
                        items = [i for i in items if (i.get("assigned_to") or {}).get("id") == ME["id"]]
                    if q.get("status_id") == ["open"]:
                        items = [i for i in items if i["status"]["id"] not in CLOSED]
                    off, lim = int(q.get("offset", ["0"])[0]), int(q.get("limit", ["25"])[0])
                    return self._send(200, {"issues": items[off:off + lim], "total_count": len(items),
                                            "offset": off, "limit": lim})
                if u.path.startswith("/issues/") and u.path.endswith(".json"):
                    iid = int(u.path[len("/issues/"):-len(".json")])
                    it = issues.get(str(iid)) or issues.get(iid)
                    if it is None:
                        return self._send(404, {})
                    if "allowed_statuses" in q.get("include", [""])[0]:
                        ids = outer.allowed.get(iid, [s["id"] for s in STATUSES])
                        it["allowed_statuses"] = [s for s in STATUSES if s["id"] in ids]
                    if "journals" in q.get("include", [""])[0]:
                        it["journals"] = [{"id": n, "notes": t, "user": {"id": ME["id"]}}
                                          for n, t in enumerate(outer.notes.get(iid, []), 1)]
                    return self._send(200, {"issue": it})
                self._send(404, {})

            def do_PUT(self):
                if not self._auth():
                    return
                u = urlparse(self.path)
                iid = int(u.path[len("/issues/"):-len(".json")])
                body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))["issue"]
                with outer.lock:
                    outer.puts.append((iid, body))
                    it = outer.issues[iid]
                    if body.get("notes"):
                        outer.notes.setdefault(iid, []).append(body["notes"])
                    sid = body.get("status_id")
                    if sid is not None and sid in outer.allowed.get(iid, [s["id"] for s in STATUSES]):
                        it["status"] = dict(next(s for s in STATUSES if s["id"] == sid))   # 選べないものは黙って無視
                self.send_response(204)
                self.end_headers()

            def _send(self, code, body):
                raw = json.dumps(body, ensure_ascii=False).encode()
                self.send_response(code)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(raw)))
                self.end_headers()
                self.wfile.write(raw)

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        threading.Thread(target=self.server.serve_forever, daemon=True).start()
        self.url = f"http://127.0.0.1:{self.server.server_address[1]}"

    def put(self, issue: dict):
        with self.lock:
            self.issues[issue["id"]] = issue

    def close(self):
        self.server.shutdown()
        self.server.server_close()


# ---------------------------------------------------------------------------
# チェック（redmine.not_mine）
# ---------------------------------------------------------------------------

class DefaultExampleTest(unittest.TestCase):
    """既定の例: 条件は Dagu の preconditions が判定し、change-watch は get / call / commit だけを受け持つ形。"""

    def setUp(self):
        if yaml is None:
            self.skipTest("例の設定・DAG を読むのに PyYAML が要る")
        sys.path.insert(0, str(CHANGE_WATCH.parent / "test"))
        self.tmp = Path(tempfile.mkdtemp())
        self.rm = FakeRedmine()
        self.addCleanup(self.rm.close)
        d = HERE.parent
        cfg = yaml.safe_load((d / "change-watch.yaml.example").read_text(encoding="utf-8"))
        self.assertNotIn("rules", cfg)                                   # 条件は change-watch に書かない
        cfg["state_dir"] = str(self.tmp / "state")
        src = cfg["sources"]["redmine-my-issues"]
        for k in ("schedule", "jitter_sec"):
            src.pop(k)
        src.update(retry={"attempts": 1}, failure_backoff_sec=0)
        self.cfg = self.tmp / "change-watch.yaml"
        self.cfg.write_text(yaml.safe_dump(cfg, allow_unicode=True), encoding="utf-8")
        self.conn = self.tmp / "connections.yaml"
        self.conn.write_text("redmine:\n  - label: default\n    url: ${REDMINE_URL}\n    token: ${REDMINE_API_KEY}\n",
                             encoding="utf-8")
        self.env = {**os.environ, "REDMINE_URL": self.rm.url, "REDMINE_API_KEY": KEY, "REDMINE_CLOSED_STATUS_ID": "5"}
        self.dag = d / "change-watch-auto-close.yaml.example"

    def run_dag(self) -> dict:
        import dagrun
        dagrun.check_shape(self, dagrun.load(self.dag), self.dag.name)
        return dagrun.run(self.dag, aw=[sys.executable, str(CHANGE_WATCH)], head=[], env=self.env,
                          paths={"/home/you/.github/connections.yaml": str(self.conn),
                                 "/home/you/.config/change-watch/redmine-ticket-agent/change-watch.yaml": str(self.cfg)})

    def test_closes_matching_tickets_once(self):
        self.rm.put(make_issue(1, "【自動完了】ログの整理"))
        self.rm.put(make_issue(2, "【重複】ログの整理"))
        self.rm.put(make_issue(3, "ふつうのチケット"))
        self.assertEqual(self.run_dag(), {"get": 0, "auto-close": 0, "duplicate": None, "commit": 0})
        runs = [self.run_dag() for _ in range(5)]                        # 1 回の起動で 1 件（完了にして一覧から消えた分も見る）
        self.assertIn({"get": 0, "auto-close": None, "duplicate": 0, "commit": 0}, runs)
        self.assertEqual(runs[-1]["commit"], None)                       # 見終えたら、もう何もしない
        self.assertEqual(self.rm.notes, {1: ["自動で完了にしました。"], 2: ["重複のため完了にしました。"]})
        self.assertEqual({i: self.rm.issues[i]["status"]["id"] for i in (1, 2, 3)}, {1: 5, 2: 5, 3: 1})
        self.assertEqual(len(self.rm.puts), 2)

    def test_maintenance_runs(self):
        import dagrun
        gc = HERE.parent / "change-watch-redmine-ticket-agent-maintenance.yaml.example"
        dagrun.check_shape(self, dagrun.load(gc), gc.name)
        self.assertEqual(dagrun.run(gc, aw=[sys.executable, str(CHANGE_WATCH)], head=[], env=self.env, paths={
            "/home/you/.github/connections.yaml": str(self.conn),
            "/home/you/.config/change-watch/redmine-ticket-agent/change-watch.yaml": str(self.cfg)}), {"gc": 0})


if __name__ == "__main__":
    unittest.main()
