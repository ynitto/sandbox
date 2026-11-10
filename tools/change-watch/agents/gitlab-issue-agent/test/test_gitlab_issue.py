"""gitlab-issue-agent のテスト（標準ライブラリだけ。`python -m unittest discover -s test`）。

設定とワークフローの例を、本物の change-watch で Dagu と同じ順（get → 条件で分ける → commit）に回したときの流れを確かめる。
GitLab は手元の HTTP サーバで偽装し、エージェント CLI（Dagu の harness.run）の代わりに、テストが偽の GitLab を書き換える。
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

HERE = Path(__file__).resolve().parent
CHANGE_WATCH = HERE.parents[2] / "change_watch.py"   # tools/change-watch/agents/<エージェント>/test


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

    def comment(self, iid: int, author: str, updated: str, labels=None):
        """コメントする（コメント数と updated_at が進み、notes の先頭に入る）。labels を渡せばラベルも変える。"""
        with self.lock:
            it = self.world["issues"][str(iid)]
            it["user_notes_count"] += 1
            it["updated_at"] = updated
            if labels is not None:
                it["labels"] = list(labels)
            self.world["notes"].setdefault(str(iid), []).insert(
                0, {"created_at": "2999-01-01T00:00:00Z", "author": {"username": author}})

    def close(self):
        self.server.shutdown()
        self.server.server_close()


class DefaultExampleTest(unittest.TestCase):
    """既定の例: 条件は Dagu の preconditions が判定し（新しい issue と変わった issue で分ける）、change-watch は get / commit だけ。"""

    DIR = HERE.parent

    def setUp(self):
        try:
            import yaml
        except ImportError:
            self.skipTest("例の設定・DAG を読むのに PyYAML が要る")
        sys.path.insert(0, str(CHANGE_WATCH.parent / "test"))
        self.tmp = Path(tempfile.mkdtemp())
        self.gl = FakeGitLab()
        self.addCleanup(self.gl.close)
        self.gl.put(make_issue(1))
        self.conn = self.tmp / "connections.json"
        self.conn.write_text(json.dumps({"gitlab": [{"label": "default", "url": self.gl.url, "token": "t0ken"}]}),
                             encoding="utf-8")
        cfg = yaml.safe_load((self.DIR / "change-watch.yaml.example").read_text(encoding="utf-8"))
        self.assertNotIn("rules", cfg)                     # 条件は change-watch に書かない
        cfg["state_dir"] = str(self.tmp / "aw-state")
        src = cfg["sources"]["gitlab-issues"]
        for k in ("schedule", "jitter_sec", "busy_wait_sec"):  # 試験ではいつでも取得する
            src.pop(k)
        src.update(retry={"attempts": 1}, failure_backoff_sec=0)
        self.cfg = self.tmp / "change-watch.json"
        self.cfg.write_text(json.dumps(cfg), encoding="utf-8")
        self.env = {**os.environ, "GITLAB_PROJECT": "g/p"}

    def run_dag(self, agent=None, exit_code: int = 0) -> tuple[str, str] | None:
        """1 回の起動。エージェントを動かしたら (ステップの名前, プロンプト)。"""
        import dagrun
        path = self.DIR / "change-watch-issue-agent.yaml.example"
        dagrun.check_shape(self, dagrun.load(path), path.name)
        seen = []

        def handle(step, vars):
            self.assertEqual((step["action"], step["with"]["stdin"]), ("harness.run", "${CHANGE}"))
            seen.append((step["name"], dagrun._expand(step["with"]["prompt"], vars)))
            if agent:
                agent()
            return exit_code

        r = dagrun.run(path, aw=[sys.executable, str(CHANGE_WATCH)], head=[], handle=handle, env=self.env,
                       paths={"/home/you/.github/connections.yaml": str(self.conn),
                              "/home/you/.config/change-watch/gitlab-issue-agent/change-watch.yaml": str(self.cfg)})
        self.assertEqual([n for n, c in r.items() if c not in (None, 0) and n not in ("new", "update")], [], r)
        self.assertEqual(len(seen) <= 1, True, seen)                         # 分かれた先のどちらか 1 つだけ
        return seen[0] if seen else None

    def test_example_runs(self):
        self.assertIsNone(self.run_dag())                                     # 初回は今ある issue を覚えるだけ
        self.gl.put(make_issue(2, labels=["todo"]))
        step, prompt = self.run_dag(agent=lambda: self.gl.comment(2, "bot", "2026-11-02T00:00:00Z"))
        self.assertEqual(step, "new")                                         # 新しい issue
        self.assertIn("新しい issue #2「issue 2」", prompt)
        self.assertIsNone(self.run_dag())                                     # エージェント自身のコメントでは起きない
        self.gl.comment(2, "alice", "2026-11-03T00:00:00Z")
        step, prompt = self.run_dag()
        self.assertEqual(step, "update")                                      # 変わった issue は別のステップ
        self.assertIn("issue #2「issue 2」が変わりました", prompt)
        self.gl.put(make_issue(3, state="closed"))
        self.assertIsNone(self.run_dag())                                     # 条件（opened）に合わない。commit はする
        self.gl.comment(1, "alice", "2026-11-04T00:00:00Z")
        self.assertEqual(self.run_dag(exit_code=1)[0], "update")              # 失敗しても commit する
        self.assertIsNone(self.run_dag())


# ---------------------------------------------------------------------------
# examples/gitlab-idd（gitlab-idd スキルと繋ぐ設定の例）
# ---------------------------------------------------------------------------

EXAMPLE = HERE.parent / "examples" / "gitlab-idd"
try:
    import yaml
    HAVE_YAML = True
except ImportError:
    HAVE_YAML = False


@unittest.skipUnless(CHANGE_WATCH.is_file() and HAVE_YAML, "change-watch か PyYAML が無い")
class IddExampleTest(unittest.TestCase):
    """設定とワークフローの例をそのまま使い、Dagu の条件（ラベル）でワーカーとレビュアーに分かれながら 1 つのイシューが進むことを確かめる。"""

    def setUp(self):
        sys.path.insert(0, str(CHANGE_WATCH.parent / "test"))
        self.tmp = Path(tempfile.mkdtemp())
        self.gl = FakeGitLab()
        self.addCleanup(self.gl.close)
        self.gl.put(make_issue(1))
        self.conn = self.tmp / "connections.json"
        self.conn.write_text(json.dumps({"gitlab": [{"label": "watch", "url": self.gl.url, "token": "t0ken"},
                                                    {"label": "default", "url": self.gl.url + "/g/p", "token": "t0ken"}]}),
                             encoding="utf-8")
        cfg = yaml.safe_load((EXAMPLE / "change-watch.yaml.example").read_text(encoding="utf-8"))
        self.assertNotIn("rules", cfg)                     # 絞り込みと分岐は change-watch に書かない
        cfg["state_dir"] = str(self.tmp / "aw-state")
        src = cfg["sources"]["gitlab-issues"]
        for k in ("schedule", "jitter_sec", "busy_wait_sec"):  # 試験ではいつでも取得する
            src.pop(k)
        src.update(retry={"attempts": 1}, failure_backoff_sec=0)
        self.cfg = self.tmp / "change-watch.json"
        self.cfg.write_text(json.dumps(cfg), encoding="utf-8")
        self.env = {**os.environ, "GITLAB_PROJECT": "g/p"}

    def run_dag(self, **agents) -> list[tuple[str, str]]:
        """1 回の起動。動いたエージェントのステップと、そのプロンプトの一覧。agents はステップの名前ごとのエージェントの動き。"""
        import dagrun
        path = EXAMPLE / "change-watch-idd.yaml.example"
        dagrun.check_shape(self, dagrun.load(path), path.name)
        seen = []

        def handle(step, vars):
            self.assertEqual(step["action"], "harness.run")
            seen.append((step["name"], dagrun._expand(step["with"]["prompt"], vars)))
            if step["name"] in agents:
                agents[step["name"]]()
            return 0

        r = dagrun.run(path, aw=[sys.executable, str(CHANGE_WATCH)], head=[], handle=handle, env=self.env,
                       paths={"/home/you/.github/connections.yaml": str(self.conn),
                              "/home/you/.config/change-watch/gitlab-issue-agent/change-watch.yaml": str(self.cfg)})
        self.assertEqual([n for n, c in r.items() if c not in (None, 0)], [], r)
        return seen

    def test_worker_reviewer_rework_and_approval(self):
        self.assertEqual(self.run_dag(), [])                                   # 初回は覚えるだけ
        self.gl.put(make_issue(2, labels=["status:open", "assignee:any"]))
        # ワーカー: 実装して status:review-ready にする。自分の書き込みでも、レビュアーの側には変化として届く（同じ起動で動く）
        seen = self.run_dag(work=lambda: self.gl.comment(2, "bot", "2026-11-02T01:00:00Z", ["status:review-ready"]),
                            review=lambda: self.gl.comment(2, "bot", "2026-11-02T02:00:00Z", ["status:needs-rework"]))
        self.assertEqual([n for n, _ in seen], ["work", "review"])
        self.assertIn("イシュー #2「issue 2」を、gitlab-idd スキルのワーカーロールで処理してください", seen[0][1])
        self.assertIn("レビュアーロールで", seen[1][1])
        # レビュアーが差し戻した（status:needs-rework）ので、次はワーカー。直して status:review-ready に → レビュアーが承認
        seen = self.run_dag(work=lambda: self.gl.comment(2, "bot", "2026-11-02T03:00:00Z", ["status:review-ready"]),
                            review=lambda: self.gl.comment(2, "bot", "2026-11-02T04:00:00Z", ["status:approved"]))
        self.assertEqual([n for n, _ in seen], ["work", "review"])
        self.assertEqual(self.run_dag(), [])                                   # status:approved はどちらの条件にも合わない
        self.assertEqual(self.run_dag(), [])

    def test_clarification_waits_for_an_answer(self):
        self.run_dag()
        self.gl.put(make_issue(3, labels=["status:open", "assignee:any"]))
        # ワーカー: 曖昧なので質問して status:needs-clarification に（ロールは同じワーカーなので、自分の書き込みでは起きない）
        seen = self.run_dag(work=lambda: self.gl.comment(3, "bot", "2026-11-03T01:00:00Z", ["status:needs-clarification"]))
        self.assertEqual([n for n, _ in seen], ["work"])
        self.assertEqual(self.run_dag(), [])
        self.gl.comment(3, "alice", "2026-11-03T02:00:00Z")                   # 依頼者が答えた
        self.assertEqual([n for n, _ in self.run_dag()], ["work"])

    def test_other_labels_are_not_handled(self):
        self.run_dag()
        self.gl.put(make_issue(4, labels=["status:in-progress"]))
        self.assertEqual(self.run_dag(), [])
        self.gl.put(make_issue(4, labels=["status:review-ready"]))             # 人がレビュー待ちにした
        self.assertEqual([n for n, _ in self.run_dag()], ["review"])


if __name__ == "__main__":
    unittest.main()
