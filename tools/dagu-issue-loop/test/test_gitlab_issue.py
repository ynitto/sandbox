"""dagu-issue-loop のテスト（標準ライブラリだけ。`python -m unittest discover -s test`）。

gitlab_issue.py（api-watch のプラグイン）のフィルタ・チェックと、本物の api-watch から Dagu の子のワークフローと同じ順
（due → begin → render → 処理 → finish）で呼んだときの流れを確かめる。GitLab は手元の HTTP サーバで偽装し、
エージェント CLI（Dagu の harness.run）の代わりに、テストが偽の GitLab を書き換える。
"""
from __future__ import annotations

import importlib.util
import json
import os
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

HERE = Path(__file__).resolve().parent
PLUGIN = HERE.parent / "gitlab_issue.py"
PROMPT = HERE.parent / "prompt.md.example"
API_WATCH = HERE.parent.parent / "api-watch" / "api_watch.py"
spec = importlib.util.spec_from_file_location("gitlab_issue", PLUGIN)
gi = importlib.util.module_from_spec(spec)
spec.loader.exec_module(gi)


def make_issue(iid: int, updated: str = "2026-11-01T00:00:00Z", **kw) -> dict:
    base = {"iid": iid, "project_id": 7, "title": f"issue {iid}", "state": "opened", "labels": [], "assignees": [],
            "milestone": None, "due_date": None, "user_notes_count": 0, "description": "",
            "updated_at": updated, "web_url": f"https://gl.example/g/p/-/issues/{iid}"}
    base.update(kw)
    return base


class FakeGitLab:
    """issue の一覧・1 件・notes・/user。world は辞書で持ち、テストが書き換える。"""

    def __init__(self):
        self.world = {"issues": {}, "notes": {}, "self": "bot"}
        self.requests: list[str] = []
        self.lock = threading.Lock()
        outer = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *a):
                pass

            def do_GET(self):
                u = urlparse(self.path)
                outer.requests.append(u.path)
                if self.headers.get("PRIVATE-TOKEN") != "t0ken":
                    return self._send(401, {"message": "401"})
                with outer.lock:
                    w = json.loads(json.dumps(outer.world))
                if u.path.endswith("/user"):
                    return self._send(200, {"username": w["self"]})
                if "/issues" in u.path:
                    tail = u.path.split("/issues", 1)[1].strip("/")
                    if not tail:
                        q = parse_qs(u.query)
                        items = sorted(w["issues"].values(), key=lambda i: i["iid"])
                        if q.get("state", [""])[0] == "opened":
                            items = [i for i in items if i["state"] == "opened"]
                        return self._send(200, items)
                    if tail.endswith("/notes"):
                        return self._send(200, w["notes"].get(tail.split("/")[0], []))
                    it = w["issues"].get(tail)
                    return self._send(200, it) if it else self._send(404, {"message": "404"})
                self._send(404, {})

            def _send(self, code, body):
                raw = json.dumps(body).encode()
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
            self.world["issues"][str(issue["iid"])] = issue

    def comment(self, iid: int, author: str, updated: str):
        """コメントする（コメント数と updated_at が進み、notes の先頭に入る）。"""
        with self.lock:
            it = self.world["issues"][str(iid)]
            it["user_notes_count"] += 1
            it["updated_at"] = updated
            self.world["notes"].setdefault(str(iid), []).insert(
                0, {"created_at": "2999-01-01T00:00:00Z", "author": {"username": author}})

    def close(self):
        self.server.shutdown()
        self.server.server_close()


# ---------------------------------------------------------------------------
# フィルタ（プロンプトの差分）
# ---------------------------------------------------------------------------

class FilterTest(unittest.TestCase):
    def test_changes_since_last_handled(self):
        ev = {"record": make_issue(1, title="new", labels=["a", "b"], assignees=[{"username": "u"}]),
              "kinds": ["changed"], "before": {"title": "x"},
              "refreshed": {"last_handled": {"title": "old", "labels": ["a"], "assignees[*].username": []}}}
        self.assertEqual(gi.gitlab_issue_changes(ev).splitlines(),
                         ["- タイトル: old → new", "- ラベル追加: b", "- 担当: u"])
        self.assertEqual(gi.gitlab_issue_kind(ev), "更新")

    def test_falls_back_to_before_and_new_issue(self):
        ev = {"record": make_issue(1, labels=["todo"]), "kinds": ["changed"], "before": {"labels": []}}
        self.assertEqual(gi.gitlab_issue_changes(ev), "- ラベル追加: todo")
        ev = {"record": make_issue(1), "kinds": ["appeared"], "before": None, "refreshed": {"last_handled": None}}
        self.assertEqual(gi.gitlab_issue_changes(ev), "- 新しい issue")
        self.assertEqual(gi.gitlab_issue_kind(ev), "新規")

    def test_needs_event(self):
        with self.assertRaises(ValueError):
            gi.gitlab_issue_changes("x")


# ---------------------------------------------------------------------------
# チェック（実行中に自分以外が動いたか）
# ---------------------------------------------------------------------------

class FakeCtx:
    def __init__(self, notes, me="bot", options=None, started=1000.0):
        self.event = {"record": make_issue(3)}
        self.delivery = {"started_at": started}
        self.options = options or {}
        self.conn = {"url": "https://gl.example"}
        self.calls: list[str] = []
        self.notes, self.me = notes, me
        self.logs: list[str] = []

    def get_json(self, url, headers=None, query=None):
        self.calls.append(url)
        return {"username": self.me} if url.endswith("/user") else self.notes

    def log(self, msg):
        self.logs.append(msg)


def note(at: float, who: str) -> dict:
    return {"created_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(at)), "author": {"username": who}}


class OthersActedTest(unittest.TestCase):
    def test_other_author_after_start(self):
        ctx = FakeCtx([note(2000, "alice"), note(1500, "bot")])
        self.assertTrue(gi.others_acted(ctx))
        self.assertEqual(ctx.calls[-1], "https://gl.example/api/v4/projects/7/issues/3/notes")

    def test_only_self_or_before_start(self):
        self.assertFalse(gi.others_acted(FakeCtx([note(2000, "bot"), note(500, "alice")])))

    def test_unknown_self_does_not_loop(self):
        ctx = FakeCtx([note(2000, "alice")], me="")
        self.assertFalse(gi.others_acted(ctx))
        self.assertIn("自分のユーザー名", ctx.logs[0])

    def test_self_username_option_skips_user_lookup(self):
        ctx = FakeCtx([note(2000, "bot")], me="unused", options={"self_username": "bot"})
        self.assertFalse(gi.others_acted(ctx))
        self.assertFalse(any(c.endswith("/user") for c in ctx.calls))


# ---------------------------------------------------------------------------
# 本物の api-watch から、Dagu の子のワークフローと同じ順で呼ぶ
# ---------------------------------------------------------------------------

@unittest.skipUnless(API_WATCH.is_file(), "api-watch が無い")
class FlowTest(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.gl = FakeGitLab()
        self.addCleanup(self.gl.close)
        self.gl.put(make_issue(1))
        self.conn = self.tmp / "connections.json"
        self.conn.write_text(json.dumps({"gitlab": [{"label": "default", "url": self.gl.url, "token": "t0ken"}]}),
                             encoding="utf-8")
        self.cfg = self.tmp / "api-watch.json"
        self.write_config()
        self.env = {**os.environ, "GITLAB_PROJECT": "g/p"}

    def write_config(self, projects=("issue-agent",)):
        target = {"name": "run", "external": True, "input_template": str(PROMPT), "lock": "agent-session",
                  "refresh": {"url": "{{ conn.url }}/api/v4/projects/{{ env.GITLAB_PROJECT | urlencode }}"
                                     "/issues/{{ event.record_id }}"},
                  "handled_before_if": ["gitlab.others_acted"],
                  "timeout_sec": 60, "max_attempts": 3, "retry_backoff_sec": 0, "order": "none"}
        rule = {"source": "gitlab-issues", "id": "iid",
                "watch": ["title", "state", "labels", "assignees[*].username", "milestone.title", "due_date",
                          "user_notes_count", "description"],
                "unordered": ["labels"], "on": ["appeared", "changed"], "filter": {"path": "state", "value": "opened"},
                "targets": [target]}
        self.cfg.write_text(json.dumps({
            "version": 1, "state_dir": str(self.tmp / "aw-state"),
            "sources": {"gitlab-issues": {
                "connection": "gitlab",
                "request": {"url": "{{ conn.url }}/api/v4/projects/{{ env.GITLAB_PROJECT | urlencode }}/issues",
                            "headers": {"PRIVATE-TOKEN": "{{ conn.token }}"},
                            "query": {"state": "opened", "scope": "all"}},
                "key": "iid", "retry": {"attempts": 1}, "failure_backoff_sec": 0,
                "min_interval_sec": 0 if len(projects) == 1 else 1, "busy_wait_sec": 0 if len(projects) == 1 else 1}},
            "rules": {p: json.loads(json.dumps(rule)) for p in projects},
        }), encoding="utf-8")

    def aw(self, *args) -> str:
        r = subprocess.run([sys.executable, str(API_WATCH), "--plugin", str(PLUGIN), "--config", str(self.cfg),
                            "--connections", str(self.conn), *args], env=self.env, capture_output=True, text=True,
                           encoding="utf-8", errors="replace", timeout=120)
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        return r.stdout

    def poll(self, rule="issue-agent"):
        """親のワークフローの fetch → evaluate → due。実行を待つ受け渡しの ID を返す。"""
        self.aw("fetch", "--force")
        self.aw("evaluate", "--rule", rule)
        return json.loads(self.aw("due", "--rule", rule).strip())

    def child(self, delivery: str, agent=None, failed=False):
        """子のワークフロー: begin → render → handle（agent）→ finish。(判断, プロンプト) を返す。"""
        decision = self.aw("begin", delivery).strip()
        prompt = None
        if decision == "run":
            prompt = self.aw("render", delivery)
            if agent:
                agent()
            if failed:
                self.aw("finish", delivery, "--failed", "--message", "harness が失敗")
            else:
                self.aw("finish", delivery)
        else:
            self.aw("finish", delivery)                  # handler_on success（begin しなかったら何もしない）
        return decision, prompt

    def test_new_issue_runs_once_and_own_comment_does_not_retrigger(self):
        self.assertEqual(self.poll(), [])                # 初回は覚えるだけ
        self.gl.put(make_issue(2, labels=["todo"]))
        ids = self.poll()
        self.assertEqual(len(ids), 1)
        decision, prompt = self.child(ids[0], agent=lambda: self.gl.comment(2, "bot", "2026-11-02T00:00:00Z"))
        self.assertEqual(decision, "run")
        self.assertIn("GitLab の issue #2「issue 2」に変化がありました（新規、1 回目）", prompt)
        self.assertIn("- 新しい issue", prompt)
        self.assertIn('"web_url": "https://gl.example/g/p/-/issues/2"', prompt)
        ids = self.poll()                                # エージェント自身のコメントを検知するが
        self.assertEqual([self.child(i)[0] for i in ids], ["skip"])      # 流さない
        self.assertEqual(self.poll(), [])

    def test_comment_by_someone_else_during_run_runs_again(self):
        self.poll()
        self.gl.put(make_issue(1, labels=["todo"], updated="2026-11-01T05:00:00Z"))
        ids = self.poll()
        self.child(ids[0], agent=lambda: self.gl.comment(1, "alice", "2026-11-01T06:00:00Z"))
        ids = self.poll()
        decision, prompt = self.child(ids[0])
        self.assertEqual(decision, "run")
        self.assertIn("- コメント +1", prompt)            # 前回処理し終えた（実行前の）状態からの差分

    def test_failure_is_retried(self):
        self.poll()
        self.gl.put(make_issue(1, labels=["todo"], updated="2026-11-01T05:00:00Z"))
        ids = self.poll()
        self.assertEqual(self.child(ids[0], failed=True)[0], "run")
        again = self.poll()                              # 失敗したものは、次の起動でもう一度出る
        self.assertEqual(again, ids)
        decision, prompt = self.child(again[0])
        self.assertIn("2 回目", prompt)
        self.assertEqual(self.poll(), [])

    def test_closed_while_waiting_is_skipped(self):
        self.poll()
        self.gl.put(make_issue(1, labels=["todo"], updated="2026-11-01T05:00:00Z"))
        ids = self.poll()
        self.gl.put(make_issue(1, labels=["todo"], state="closed", updated="2026-11-01T06:00:00Z"))
        self.assertEqual(self.child(ids[0])[0], "skip")  # 実行の直前の取り直しで filter から外れる

    def test_one_agent_session_across_projects(self):
        self.write_config(projects=("project-a", "project-b"))
        self.poll("project-a")
        self.poll("project-b")
        self.gl.put(make_issue(1, labels=["todo"], updated="2026-11-01T05:00:00Z"))
        a = self.poll("project-a")[0]
        b = self.poll("project-b")[0]
        self.assertEqual(self.aw("begin", a).strip(), "run")
        self.assertEqual(self.aw("begin", b).strip(), "busy")   # 同じ lock を持つ受け渡し先は同時に 1 つ
        self.aw("finish", a)


if __name__ == "__main__":
    unittest.main()
