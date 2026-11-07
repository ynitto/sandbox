"""redmine-ticket-agent のテスト（標準ライブラリと PyYAML。`python -m unittest discover -s test`）。

redmine_issue.py のチェック（redmine.not_mine）と close、設定の例（change-watch.yaml.example）をそのまま本物の change-watch で
Dagu のワークフローと同じ順（fetch → evaluate → work）に回したときの流れを確かめる。Redmine は手元の HTTP サーバで偽装する。
"""
from __future__ import annotations

import importlib.util
import json
import os
import subprocess
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
PLUGIN = HERE.parent / "redmine_issue.py"
EXAMPLE = HERE.parent / "change-watch.yaml.example"
CHANGE_WATCH = HERE.parents[2] / "change_watch.py"   # tools/change-watch/agents/<エージェント>/test
spec = importlib.util.spec_from_file_location("redmine_issue", PLUGIN)
ri = importlib.util.module_from_spec(spec)
spec.loader.exec_module(ri)

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

class FakeCtx:
    def __init__(self, issue, options=None):
        self.event = {"record": issue}
        self.options = options or {}
        self.conn = {"url": "https://rm.example/"}
        self.calls: list[str] = []
        self.logs: list[str] = []

    def get_json(self, url, headers=None, query=None):
        self.calls.append(url)
        return {"user": {"id": ME["id"]}}

    def log(self, msg):
        self.logs.append(msg)


class NotMineTest(unittest.TestCase):
    def test_mine(self):
        ctx = FakeCtx(make_issue(1))
        self.assertFalse(ri.not_mine(ctx))
        self.assertEqual(ctx.calls, ["https://rm.example/users/current.json"])

    def test_someone_else_or_nobody(self):
        self.assertTrue(ri.not_mine(FakeCtx(make_issue(1, assignee=ALICE))))
        self.assertTrue(ri.not_mine(FakeCtx(make_issue(1, assignee=None))))

    def test_user_id_option_skips_lookup(self):
        ctx = FakeCtx(make_issue(1, assignee=ALICE), {"user_id": 9})
        self.assertFalse(ri.not_mine(ctx))
        self.assertEqual(ctx.calls, [])


# ---------------------------------------------------------------------------
# close
# ---------------------------------------------------------------------------

class CloseTest(unittest.TestCase):
    def setUp(self):
        self.rm = FakeRedmine()
        self.addCleanup(self.rm.close)
        self.api = ri.Redmine(self.rm.url, KEY)

    def test_comments_and_closes_in_one_put(self):
        self.rm.put(make_issue(1, "自動完了 してほしい"))
        self.assertEqual(ri.close(self.api, self.rm.issues[1], "完了", "済み", log=lambda m: None), ri.OK)
        self.assertEqual(self.rm.puts, [(1, {"notes": "済み", "status_id": 5})])
        self.assertEqual(self.rm.issues[1]["status"]["name"], "完了")

    def test_status_by_id_and_already_closed_is_noop(self):
        self.rm.put(make_issue(1, status=5))
        self.assertEqual(ri.close(self.api, self.rm.issues[1], "5", "済み", log=lambda m: None), ri.OK)
        self.assertEqual(self.rm.puts, [])

    def test_already_closed_with_another_status_is_noop(self):
        # 上のルールが先に「却下」にしたチケット。下のルールの close はコメントもステータスも変えない
        for legacy in (False, True):
            with self.subTest(legacy=legacy):
                self.rm.legacy = legacy
                self.rm.put(make_issue(1, status=1))
                ri.close(self.api, self.rm.issues[1], "却下", "上のルール", log=lambda m: None)
                self.rm.puts.clear()
                self.assertEqual(ri.close(ri.Redmine(self.rm.url, KEY), self.rm.issues[1], "完了", "下のルール",
                                          log=lambda m: None), ri.OK)
                self.assertEqual(self.rm.puts, [])
                self.assertEqual(self.rm.notes.pop(1), ["上のルール"])

    def test_unknown_status_and_disallowed_transition_write_nothing(self):
        self.rm.put(make_issue(1))
        with self.assertRaisesRegex(ri.RedmineError, "ありません（あるもの: 新規・進行中・完了・却下）"):
            ri.close(self.api, self.rm.issues[1], "保留", "済み")
        self.rm.allowed[1] = [1, 2]
        with self.assertRaisesRegex(ri.RedmineError, "変えられません（ワークフローで選べるもの: 新規・進行中）"):
            ri.close(self.api, self.rm.issues[1], "完了", "済み")
        self.assertEqual(self.rm.puts, [])

    def test_status_ignored_by_redmine_is_a_failure(self):
        self.rm.put(make_issue(1))
        real = self.rm.allowed
        # allowed_statuses は完了を含むと答えるが、PUT では無視される（古い Redmine・権限の食い違い）
        api = ri.Redmine(self.rm.url, KEY)
        orig = api.issue
        api.issue = lambda iid, include=None: (dict(orig(iid), allowed_statuses=STATUSES) if include else orig(iid))
        real[1] = [1]
        with self.assertRaisesRegex(ri.RedmineError, "ステータスが「完了」になりませんでした"):
            ri.close(api, self.rm.issues[1], "完了", "済み")

    def test_retry_after_ignored_status_does_not_repeat_the_note(self):
        self.rm.put(make_issue(1))
        self.rm.notes[1] = ["済み"]                          # 前の試行でコメントだけが残った
        self.assertEqual(ri.close(self.api, self.rm.issues[1], "完了", "済み", log=lambda m: None), ri.OK)
        self.assertEqual(self.rm.puts, [(1, {"status_id": 5})])
        self.assertEqual(self.rm.notes[1], ["済み"])

    def test_cli_reads_env_and_event_file(self):
        self.rm.put(make_issue(3, description="本文に自動完了"))
        ev = Path(tempfile.mkdtemp()) / "ev.json"
        ev.write_text(json.dumps({"record": self.rm.issues[3]}), encoding="utf-8")
        env = {**os.environ, "REDMINE_URL": self.rm.url, "REDMINE_API_KEY": KEY, "CHANGEWATCH_EVENT_FILE": str(ev)}
        r = subprocess.run([sys.executable, str(PLUGIN), "close", "--status", "完了", "--note", "済み"],
                           env=env, capture_output=True, text=True, encoding="utf-8")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(self.rm.notes[3], ["済み"])
        env.pop("REDMINE_API_KEY")
        r = subprocess.run([sys.executable, str(PLUGIN), "close", "--status", "完了", "--note", "済み"],
                           env=env, capture_output=True, text=True, encoding="utf-8")
        self.assertEqual(r.returncode, ri.USAGE)


# ---------------------------------------------------------------------------
# 設定の例をそのまま本物の change-watch で回す
# ---------------------------------------------------------------------------

@unittest.skipUnless(CHANGE_WATCH.is_file() and yaml is not None, "change-watch か PyYAML が無い")
class FlowTest(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.rm = FakeRedmine()
        self.addCleanup(self.rm.close)
        cfg = yaml.safe_load(EXAMPLE.read_text(encoding="utf-8"))
        cfg["state_dir"] = str(self.tmp / "state")
        src = cfg["sources"]["redmine-my-issues"]
        src.update({"retry": {"attempts": 1}, "failure_backoff_sec": 0, "jitter_sec": 0})
        src.pop("schedule")
        src.update({"min_interval_sec": 1, "busy_wait_sec": 1})   # ルールが 2 本あるので schedule の代わりに要る（fetch は --force）
        self.assertEqual(list(cfg["rules"]), ["auto-close", "auto-close-duplicate"])
        for rule in cfg["rules"].values():
            target = rule["targets"][0]
            self.assertEqual(target["command"][1], "/home/you/.local/share/change-watch/agents/redmine-ticket-agent/redmine_issue.py")
            target["command"][0:2] = [sys.executable, str(PLUGIN)]
            target["retry_backoff_sec"] = 0
        self.cfg = self.tmp / "change-watch.yaml"
        self.cfg.write_text(yaml.safe_dump(cfg, allow_unicode=True), encoding="utf-8")
        self.conn = self.tmp / "connections.yaml"
        self.conn.write_text("redmine:\n  - label: default\n    url: ${REDMINE_URL}\n    token: ${REDMINE_API_KEY}\n",
                             encoding="utf-8")
        self.env = {**os.environ, "REDMINE_URL": self.rm.url, "REDMINE_API_KEY": KEY}

    def aw(self, *args) -> str:
        r = subprocess.run([sys.executable, str(CHANGE_WATCH), "--plugin", str(PLUGIN), "--config", str(self.cfg),
                            "--connections", str(self.conn), *args],
                           env=self.env, capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=120)
        self.assertIn(r.returncode, (0, 4), r.stdout + r.stderr)
        return r.stderr

    def cycle(self, between=None) -> str:
        self.aw("fetch", "--force")
        self.aw("evaluate", "--source", "redmine-my-issues")       # 例のワークフローと同じく、取得元のルールを全部回す
        if between:
            between()
        return self.aw("work", "--source", "redmine-my-issues")

    def test_closes_only_my_matching_tickets_once(self):
        self.rm.put(make_issue(1, "【自動完了】ログの整理"))
        self.rm.put(make_issue(2, "ふつうの作業"))
        self.rm.put(make_issue(3, "ふつう", description="手順: 自動完了 で閉じる"))
        self.rm.put(make_issue(4, "【自動完了】ほかの人の", assignee=ALICE))
        self.cycle()                                         # initial: emit なので、いま当たるものも完了にする
        self.assertEqual(sorted(self.rm.notes), [1, 3])
        self.assertEqual(self.rm.notes[1], ["自動で完了にしました。"])
        self.assertEqual(self.rm.issues[1]["status"]["name"], "完了")
        self.assertEqual(self.rm.issues[2]["status"]["name"], "新規")
        self.assertEqual(self.rm.issues[4]["status"]["name"], "新規")
        self.cycle()
        self.assertEqual(len(self.rm.puts), 2)               # 2 回目は何もしない

    def test_each_pair_writes_its_own_comment(self):
        self.rm.put(make_issue(1, "【自動完了】ログの整理"))
        self.rm.put(make_issue(2, "【重複】#1 と同じ"))
        self.rm.put(make_issue(3, "重複かもしれない"))             # 【重複】ではない
        self.cycle()
        self.assertEqual(self.rm.notes, {1: ["自動で完了にしました。"], 2: ["重複のため完了にしました。"]})
        self.assertEqual(self.rm.issues[2]["status"]["name"], "完了")
        self.assertEqual(self.rm.issues[3]["status"]["name"], "新規")

    def test_ticket_matching_two_pairs_gets_the_upper_comment_once(self):
        cfg = yaml.safe_load(self.cfg.read_text(encoding="utf-8"))
        cmd = cfg["rules"]["auto-close-duplicate"]["targets"][0]["command"]
        cmd[cmd.index("--status") + 1] = "却下"                   # 下の対は別の完了扱いのステータスにする
        self.cfg.write_text(yaml.safe_dump(cfg, allow_unicode=True), encoding="utf-8")
        for legacy in (False, True):                             # 5.1 より前は filter の is_closed が効かず、close が止める
            with self.subTest(legacy=legacy):
                self.rm.legacy = legacy
                iid = 10 + int(legacy)
                self.rm.put(make_issue(iid, "【重複】自動完了の手順"))
                self.cycle()
                self.assertEqual(self.rm.notes[iid], ["自動で完了にしました。"])
                self.assertEqual(self.rm.issues[iid]["status"]["name"], "完了")
                self.assertEqual(len([x for x in self.rm.puts if x[0] == iid]), 1)

    def test_ticket_edited_to_match_later_is_closed(self):
        self.rm.put(make_issue(1, "ふつう"))
        self.cycle()
        self.assertEqual(self.rm.puts, [])
        self.rm.put(make_issue(1, "ふつう（自動完了）"))
        self.cycle()
        self.assertEqual(self.rm.issues[1]["status"]["name"], "完了")

    def test_changing_the_keyword_closes_tickets_that_now_match(self):
        self.rm.put(make_issue(1, "【片付け】古いブランチ"))
        self.cycle()
        self.assertEqual(self.rm.puts, [])
        cfg = yaml.safe_load(self.cfg.read_text(encoding="utf-8"))
        cfg["rules"]["auto-close"]["filter"]["all"][0]["any"][0]["value"] = "片付け"
        self.cfg.write_text(yaml.safe_dump(cfg, allow_unicode=True), encoding="utf-8")
        self.cycle()                                         # on_rule_change: continue なので、基準にし直さず完了にする
        self.assertEqual(self.rm.issues[1]["status"]["name"], "完了")

    def test_reassigned_or_unmatched_while_waiting_is_not_closed(self):
        self.rm.put(make_issue(1, "自動完了 A"))
        self.rm.put(make_issue(2, "自動完了 B"))

        def change():
            self.rm.put(make_issue(1, "自動完了 A", assignee=ALICE))   # 担当が替わった（skip_if）
            self.rm.put(make_issue(2, "B"))                            # 文字列を消した（filter）
        self.cycle(between=change)
        self.assertEqual(self.rm.puts, [])

    def test_disallowed_status_fails_without_writing_and_retries(self):
        self.rm.put(make_issue(1, "自動完了"))
        self.rm.allowed[1] = [1, 2]
        err = self.cycle()
        self.assertIn("変えられません", err)
        self.assertEqual(self.rm.puts, [])
        self.rm.allowed.pop(1)                               # ワークフローを直した
        self.cycle()
        self.assertEqual(self.rm.notes[1], ["自動で完了にしました。"])

    def test_example_dag_runs(self):
        sys.path.insert(0, str(CHANGE_WATCH.parent / "test"))
        import dagrun
        self.rm.put(make_issue(1, "【自動完了】ログの整理"))
        dag = HERE.parent / "change-watch-auto-close.yaml.example"
        dagrun.check_shape(self, dagrun.load(dag), dag.name)
        r = dagrun.run(dag, aw=[sys.executable, str(CHANGE_WATCH)], head=["--config", str(self.cfg)],
                       paths={"/home/you/.local/share/change-watch/agents/redmine-ticket-agent/redmine_issue.py": str(PLUGIN),
                              "/home/you/.github/connections.yaml": str(self.conn),
                              "/home/you/.config/change-watch/redmine-ticket-agent/change-watch.yaml": str(self.cfg)}, env=self.env)
        self.assertEqual(r, {"fetch": 0, "evaluate": 0, "work": 0})
        self.assertEqual(self.rm.notes[1], ["自動で完了にしました。"])
        gc = HERE.parent / "change-watch-redmine-ticket-agent-maintenance.yaml.example"
        dagrun.check_shape(self, dagrun.load(gc), gc.name)
        self.assertEqual(dagrun.run(gc, aw=[sys.executable, str(CHANGE_WATCH)], head=[], env=self.env, paths={
            "/home/you/.github/connections.yaml": str(self.conn),
            "/home/you/.config/change-watch/redmine-ticket-agent/change-watch.yaml": str(self.cfg)}), {"gc": 0})


if __name__ == "__main__":
    unittest.main()
