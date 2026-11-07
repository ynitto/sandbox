"""gitlab-issue-agent のテスト（標準ライブラリだけ。`python -m unittest discover -s test`）。

gitlab_issue.py（change-watch のプラグイン）のフィルタ・チェックと、本物の change-watch を Dagu のワークフローと同じ順
（fetch → evaluate → claim → render → 処理 → finish --exit-code）で呼んだときの流れを確かめる。GitLab は手元の HTTP サーバで偽装し、
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
CHANGE_WATCH = HERE.parents[2] / "change_watch.py"   # tools/change-watch/agents/<エージェント>/test
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

    def test_idd_role(self):
        self.assertEqual(gi.gitlab_idd_role(make_issue(1, labels=["status:open", "assignee:any"])), "ワーカー")
        self.assertEqual(gi.gitlab_idd_role(make_issue(1, labels=["status:needs-rework"])), "ワーカー")
        self.assertEqual(gi.gitlab_idd_role(make_issue(1, labels=["status:review-ready"])), "レビュアー")
        with self.assertRaises(ValueError):
            gi.gitlab_idd_role(make_issue(1, labels=["status:in-progress"]))


# ---------------------------------------------------------------------------
# チェック（実行中に自分以外が動いたか）
# ---------------------------------------------------------------------------

class FakeCtx:
    def __init__(self, notes=(), me="bot", options=None, started=1000.0, record=None, now=None):
        self.event = {"record": record or make_issue(3)}
        self.now = now
        self.delivery = {"started_at": started}
        self.options = options or {}
        self.conn = {"url": "https://gl.example"}
        self.calls: list[str] = []
        self.notes, self.me = notes, me
        self.logs: list[str] = []

    def get_json(self, url, headers=None, query=None):
        self.calls.append(url)
        if url.endswith("/user"):
            return {"username": self.me}
        return self.notes if url.endswith("/notes") else self.now

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


class RoleChangedTest(unittest.TestCase):
    def ctx(self, before, after):
        return FakeCtx(record=make_issue(3, labels=before), now=make_issue(3, labels=after))

    def test_worker_to_reviewer_and_back(self):
        self.assertTrue(gi.idd_role_changed(self.ctx(["status:open"], ["status:review-ready"])))
        self.assertTrue(gi.idd_role_changed(self.ctx(["status:review-ready"], ["status:needs-rework"])))

    def test_same_role_or_out_of_idd(self):
        self.assertFalse(gi.idd_role_changed(self.ctx(["status:open"], ["status:needs-clarification"])))
        self.assertFalse(gi.idd_role_changed(self.ctx(["status:review-ready"], ["status:approved"])))
        ctx = self.ctx(["status:open"], ["status:review-ready"])
        gi.idd_role_changed(ctx)
        self.assertEqual(ctx.calls, ["https://gl.example/api/v4/projects/7/issues/3"])


# ---------------------------------------------------------------------------
# 本物の change-watch から、Dagu のワークフローと同じ順で呼ぶ
# ---------------------------------------------------------------------------

class _FlowBase(unittest.TestCase):
    """偽の GitLab と本物の change-watch。write_config で設定を書き、cycle で 1 回の起動を回す。"""

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.gl = FakeGitLab()
        self.addCleanup(self.gl.close)
        self.gl.put(make_issue(1))
        self.conn = self.tmp / "connections.json"
        self.conn.write_text(json.dumps({"gitlab": [{"label": "default", "url": self.gl.url, "token": "t0ken"}]}),
                             encoding="utf-8")
        self.cfg = self.tmp / "change-watch.json"
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
        r = subprocess.run([sys.executable, str(CHANGE_WATCH), "--plugin", str(PLUGIN), "--config", str(self.cfg),
                            "--connections", str(self.conn), "--state-dir", str(self.tmp / "aw-state"), *args],
                           env=self.env, capture_output=True, text=True,
                           encoding="utf-8", errors="replace", timeout=120)
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        return r.stdout

    def cycle(self, rule="issue-agent", agent=None, exit_code="0"):
        """1 回の起動: fetch → evaluate → claim → render → handle（agent）→ finish --exit-code。

        (受け渡しの ID、プロンプト) を返す。実行できるものが無ければ (None, None)。"""
        self.aw("fetch", "--force")
        self.aw("evaluate", "--rule", rule)
        delivery = self.aw("claim", "--rule", rule).strip()
        if not delivery:
            return None, None
        prompt = self.aw("render", delivery)
        if agent:
            agent()
        self.aw("finish", delivery, "--exit-code", exit_code)
        return delivery, prompt


    def run_dag(self, path: Path, exit_code: int = 0) -> str | None:
        """例の Dagu のワークフローを、Dagu と同じ順で 1 回動かす。handle に渡ったプロンプトを返す（処理するものが無ければ None）。"""
        try:
            import yaml  # noqa: F401
        except ImportError:
            self.skipTest("例の DAG を読むのに PyYAML が要る")
        sys.path.insert(0, str(CHANGE_WATCH.parent / "test"))
        import dagrun
        dagrun.check_shape(self, dagrun.load(path), path.name)
        seen = []

        def handle(step, vars):
            self.assertEqual((step["action"], step["with"]["stdin"]), ("harness.run", "${INPUT}"))
            seen.append(vars["INPUT"])
            return exit_code

        r = dagrun.run(path, aw=[sys.executable, str(CHANGE_WATCH)],
                       head=["--config", str(self.cfg), "--state-dir", str(self.tmp / "aw-state")],
                       paths={"/home/you/.local/share/change-watch/agents/gitlab-issue-agent/gitlab_issue.py": str(PLUGIN),
                              "/home/you/.github/connections.yaml": str(self.conn),
                              "/home/you/.config/change-watch/gitlab-issue-agent/change-watch.yaml": str(self.cfg)},
                       handle=handle, env=self.env)
        self.assertEqual([n for n, c in r.items() if c not in (None, 0) and n != "handle"], [], r)
        self.assertEqual(r["finish"], 0 if seen else None, r)      # 失敗も finish で記録する。取れなければ何もしない
        return seen[0] if seen else None


@unittest.skipUnless(CHANGE_WATCH.is_file(), "change-watch が無い")
class FlowTest(_FlowBase):
    def test_new_issue_runs_once_and_own_comment_does_not_retrigger(self):
        self.assertEqual(self.cycle(), (None, None))     # 初回は覚えるだけ
        self.gl.put(make_issue(2, labels=["todo"]))
        d, prompt = self.cycle(agent=lambda: self.gl.comment(2, "bot", "2026-11-02T00:00:00Z"))
        self.assertIsNotNone(d)
        self.assertIn("GitLab の issue #2「issue 2」に変化がありました（新規、1 回目）", prompt)
        self.assertIn("- 新しい issue", prompt)
        self.assertIn('"web_url": "https://gl.example/g/p/-/issues/2"', prompt)
        self.assertEqual(self.cycle(), (None, None))     # エージェント自身のコメントを検知するが、流さない

    def test_comment_by_someone_else_during_run_runs_again(self):
        self.cycle()
        self.gl.put(make_issue(1, labels=["todo"], updated="2026-11-01T05:00:00Z"))
        self.cycle(agent=lambda: self.gl.comment(1, "alice", "2026-11-01T06:00:00Z"))
        d, prompt = self.cycle()
        self.assertIsNotNone(d)
        self.assertIn("- コメント +1", prompt)            # 前回処理し終えた（実行前の）状態からの差分

    def test_example_dag_runs(self):
        dag = HERE.parent / "change-watch-issue-agent.yaml.example"
        self.assertIsNone(self.run_dag(dag))                     # 初回は覚えるだけ
        self.gl.put(make_issue(2, labels=["todo"]))
        self.assertIn("issue #2", self.run_dag(dag, exit_code=1))   # 失敗も finish で記録し、
        self.assertIn("2 回目", self.run_dag(dag))                  # 次の起動でもう一度
        self.assertIsNone(self.run_dag(dag))
        import dagrun
        gc = HERE.parent / "change-watch-gitlab-issue-agent-maintenance.yaml.example"
        dagrun.check_shape(self, dagrun.load(gc), gc.name)
        self.assertEqual(dagrun.run(gc, aw=[sys.executable, str(CHANGE_WATCH)],
                                    head=["--state-dir", str(self.tmp / "aw-state")], env=self.env, paths={
            "/home/you/.github/connections.yaml": str(self.conn),
            "/home/you/.config/change-watch/gitlab-issue-agent/change-watch.yaml": str(self.cfg)}), {"gc": 0})

    def test_failure_is_retried(self):
        self.cycle()
        self.gl.put(make_issue(1, labels=["todo"], updated="2026-11-01T05:00:00Z"))
        d, _ = self.cycle(exit_code="1")                 # harness.run が失敗した
        d2, prompt = self.cycle()                        # 次の起動でもう一度
        self.assertEqual(d2, d)
        self.assertIn("2 回目", prompt)
        self.assertEqual(self.cycle(), (None, None))

    def test_closed_while_waiting_is_skipped(self):
        self.cycle()
        self.gl.put(make_issue(1, labels=["todo"], updated="2026-11-01T05:00:00Z"))
        self.aw("fetch", "--force")
        self.aw("evaluate", "--rule", "issue-agent")
        self.gl.put(make_issue(1, labels=["todo"], state="closed", updated="2026-11-01T06:00:00Z"))
        self.assertEqual(self.aw("claim", "--rule", "issue-agent").strip(), "")   # 取り直しで filter から外れる

    def test_one_agent_session_across_projects(self):
        self.write_config(projects=("project-a", "project-b"))
        self.cycle("project-a")
        self.cycle("project-b")
        self.gl.put(make_issue(1, labels=["todo"], updated="2026-11-01T05:00:00Z"))
        for r in ("project-a", "project-b"):
            self.aw("fetch", "--force")
            self.aw("evaluate", "--rule", r)
        a = self.aw("claim", "--rule", "project-a").strip()
        self.assertTrue(a.startswith("aw_"))
        self.assertEqual(self.aw("claim", "--rule", "project-b").strip(), "")    # 同じ lock は同時に 1 つ
        self.aw("finish", a, "--exit-code", "0")       # project-b は試行に数えずに戻してあり、少しあとの起動で取れる


# ---------------------------------------------------------------------------
# examples/gitlab-idd（gitlab-idd スキルと繋ぐ設定の例）
# ---------------------------------------------------------------------------

EXAMPLE = HERE.parent / "examples" / "gitlab-idd"
try:
    import yaml  # noqa: F401
    HAVE_YAML = True
except ImportError:
    HAVE_YAML = False


@unittest.skipUnless(CHANGE_WATCH.is_file() and HAVE_YAML, "change-watch か PyYAML が無い")
class IddExampleTest(_FlowBase):
    """設定の例をそのまま使い、gitlab-idd のロールが入れ替わりながら 1 つのイシューが進むことを確かめる。"""

    def write_config(self, projects=None):
        self.cfg = self.tmp / "change-watch.yaml"
        self.cfg.write_text((EXAMPLE / "change-watch.yaml.example").read_text(encoding="utf-8"), encoding="utf-8")
        (self.tmp / "idd.prompt.md").write_text((EXAMPLE / "idd.prompt.md.example").read_text(encoding="utf-8"),
                                                encoding="utf-8")
        self.conn.write_text(json.dumps({"gitlab": [{"label": "watch", "url": self.gl.url, "token": "t0ken"},
                                                    {"label": "default", "url": self.gl.url + "/g/p", "token": "t0ken"}]}),
                             encoding="utf-8")

    def cycle(self, agent=None, exit_code="0"):
        return super().cycle("idd", agent=agent, exit_code=exit_code)

    def test_worker_reviewer_rework_and_approval(self):
        self.assertEqual(self.cycle(), (None, None))
        self.gl.put(make_issue(2, labels=["status:open", "assignee:any"]))
        # ワーカー: 実装して status:review-ready にする（自分の書き込みだが、ロールが変わるので次はレビュアー）
        d, prompt = self.cycle(agent=lambda: self.gl.comment(2, "bot", "2026-11-02T01:00:00Z", ["status:review-ready"]))
        self.assertIn("gitlab-idd スキルのワーカーロールで、このイシュー #2 だけを処理してください", prompt)
        # レビュアー: 差し戻す（status:needs-rework）
        d, prompt = self.cycle(agent=lambda: self.gl.comment(2, "bot", "2026-11-02T02:00:00Z", ["status:needs-rework"]))
        self.assertIn("レビュアーロールで", prompt)
        self.assertIn("ラベル追加: status:review-ready", prompt)
        # ワーカー: 直してもう一度 status:review-ready に
        d, prompt = self.cycle(agent=lambda: self.gl.comment(2, "bot", "2026-11-02T03:00:00Z", ["status:review-ready"]))
        self.assertIn("ワーカーロールで", prompt)
        # レビュアー: 承認する（status:approved。マージは人）
        d, prompt = self.cycle(agent=lambda: self.gl.comment(2, "bot", "2026-11-02T04:00:00Z", ["status:approved"]))
        self.assertIn("レビュアーロールで", prompt)
        self.assertEqual(self.cycle(), (None, None))    # status:approved は filter から外れる

    def test_clarification_waits_for_an_answer(self):
        self.cycle()
        self.gl.put(make_issue(3, labels=["status:open", "assignee:any"]))
        # ワーカー: 曖昧なので質問して status:needs-clarification に（ロールは同じワーカーなので、自分の書き込みでは起きない）
        self.cycle(agent=lambda: self.gl.comment(3, "bot", "2026-11-03T01:00:00Z", ["status:needs-clarification"]))
        self.assertEqual(self.cycle(), (None, None))
        self.gl.comment(3, "alice", "2026-11-03T02:00:00Z")   # 依頼者が答えた
        d, prompt = self.cycle()
        self.assertIn("ワーカーロールで", prompt)
        self.assertIn("- コメント +1", prompt)

    def test_example_dag_runs(self):
        self.assertEqual(self.run_dag(EXAMPLE / "change-watch-idd.yaml.example"), None)    # 初回は覚えるだけ
        self.gl.put(make_issue(2, labels=["status:open", "assignee:any"]))
        self.aw("fetch", "--force")                              # 取得元の schedule の予定の時刻まで、ワークフローの fetch は問い合わせない
        prompt = self.run_dag(EXAMPLE / "change-watch-idd.yaml.example")
        self.assertIn("ワーカーロールで、このイシュー #2 だけを処理してください", prompt)


if __name__ == "__main__":
    unittest.main()
