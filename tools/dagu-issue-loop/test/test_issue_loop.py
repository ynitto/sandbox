"""dagu-issue-loop のテスト（標準ライブラリだけ。`python -m unittest discover -s test`）。

GitLab は手元の HTTP サーバで偽装し、エージェント CLI は小さな Python スクリプトで偽装する。
handle にはイベントのファイル（api-watch が書くものと同じ形）を直接渡すか、本物の api-watch から呼ぶ。
"""
from __future__ import annotations

import importlib.util
import json
import os
import signal
import subprocess
import sys
import tempfile
import textwrap
import threading
import time
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

HERE = Path(__file__).resolve().parent
SCRIPT = HERE.parent / "issue_loop.py"
spec = importlib.util.spec_from_file_location("issue_loop", SCRIPT)
il = importlib.util.module_from_spec(spec)
sys.modules["issue_loop"] = il
spec.loader.exec_module(il)


def make_issue(iid: int, updated: str, **kw) -> dict:
    base = {"iid": iid, "title": f"issue {iid}", "state": "opened", "labels": [], "assignees": [],
            "milestone": None, "due_date": None, "user_notes_count": 0, "description": "",
            "updated_at": updated, "web_url": f"https://gl.example/g/p/-/issues/{iid}"}
    base.update(kw)
    return base


# ---------------------------------------------------------------------------
# 偽の GitLab（world.json を毎回読み直す。テストと偽エージェントが書き換える）
# ---------------------------------------------------------------------------

class FakeGitLab:
    def __init__(self, world: Path):
        self.world = world
        self.requests: list[str] = []   # 受けた GET のパス（GitLab への負荷を数える）
        outer = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *a):  # noqa: D401
                pass

            def do_GET(self):
                outer.requests.append(urlparse(self.path).path)
                w = json.loads(outer.world.read_text(encoding="utf-8"))
                if self.headers.get("PRIVATE-TOKEN") != "t0ken":
                    return self._send(401, {"message": "401"})
                u = urlparse(self.path)
                parts = u.path.split("/")
                if u.path.endswith("/user"):
                    return self._send(200, {"username": w.get("self", "bot")})
                if "/issues" in u.path:
                    tail = u.path.split("/issues", 1)[1].strip("/")
                    if not tail:
                        q = parse_qs(u.query)
                        items = list(w["issues"])
                        if "labels" in q:
                            want = set(q["labels"][0].split(","))
                            items = [i for i in items if want <= set(i["labels"])]
                        page = int(q.get("page", ["1"])[0])
                        per = w.get("per_page", 100)
                        chunk = items[(page - 1) * per: page * per]
                        nxt = str(page + 1) if page * per < len(items) else ""
                        return self._send(200, chunk, {"X-Next-Page": nxt})
                    if tail.endswith("/notes"):
                        iid = tail.split("/")[0]
                        return self._send(200, w.get("notes", {}).get(iid, []))
                    for i in w["issues"]:
                        if str(i["iid"]) == tail:
                            return self._send(200, i)
                    return self._send(404, {})
                self._send(404, {"parts": parts})

            def _send(self, code, body, headers=None):
                raw = json.dumps(body).encode()
                self.send_response(code)
                self.send_header("Content-Type", "application/json")
                for k, v in (headers or {}).items():
                    self.send_header(k, v)
                self.send_header("Content-Length", str(len(raw)))
                self.end_headers()
                self.wfile.write(raw)

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.url = f"http://127.0.0.1:{self.server.server_address[1]}"

    def close(self):
        self.server.shutdown()
        self.server.server_close()


FAKE_AGENT = textwrap.dedent('''\
    import json, os, sys, time
    world = os.environ["FAKE_WORLD"]
    prompt = sys.stdin.buffer.read().decode("utf-8")   # 送り手は UTF-8 の bytes で渡す
    with open(os.environ["FAKE_CALLS"], "a", encoding="utf-8") as f:
        f.write(json.dumps({"pid": os.getpid(), "iid": os.environ.get("ISSUE_LOOP_IID"),
                            "start": time.time(), "prompt": prompt}) + "\\n")
    time.sleep(float(os.environ.get("FAKE_SLEEP", "0")))
    mode = os.environ.get("FAKE_MODE", "ok")
    if mode == "comment":   # エージェント自身が issue にコメントする（updated_at が進む）
        w = json.load(open(world, encoding="utf-8"))
        for i in w["issues"]:
            if str(i["iid"]) == os.environ["ISSUE_LOOP_IID"]:
                i["user_notes_count"] += 1
                i["updated_at"] = "2026-11-01T10:00:00Z"
        w.setdefault("notes", {})[os.environ["ISSUE_LOOP_IID"]] = [
            {"created_at": "2999-01-01T00:00:00Z", "author": {"username": w.get("self", "bot")}}]
        json.dump(w, open(world, "w", encoding="utf-8"))
    if mode == "other":     # 実行中に別の人がコメントする
        w = json.load(open(world, encoding="utf-8"))
        for i in w["issues"]:
            if str(i["iid"]) == os.environ["ISSUE_LOOP_IID"]:
                i["user_notes_count"] += 1
                i["updated_at"] = "2026-11-01T11:00:00Z"
        w.setdefault("notes", {})[os.environ["ISSUE_LOOP_IID"]] = [
            {"created_at": "2999-01-01T00:00:00Z", "author": {"username": "alice"}}]
        json.dump(w, open(world, "w", encoding="utf-8"))
    with open(os.environ["FAKE_CALLS"], "a", encoding="utf-8") as f:
        f.write(json.dumps({"pid": os.getpid(), "end": time.time()}) + "\\n")
    sys.exit(1 if mode == "fail" else 0)
''')


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.world = self.tmp / "world.json"
        self.calls = self.tmp / "calls.jsonl"
        self.calls.touch()
        self.set_world([make_issue(1, "2026-11-01T00:00:00Z")])
        self.gl = FakeGitLab(self.world)
        agent = self.tmp / "fake_agent.py"
        agent.write_text(FAKE_AGENT, encoding="utf-8")
        self.env = {
            **os.environ,
            "HOME": str(self.tmp / "home"),
            "GITLAB_API_URL": self.gl.url + "/api/v4",
            "GITLAB_PROJECT": "g/p",
            "GITLAB_TOKEN": "t0ken",
            "ISSUE_LOOP_STATE_DIR": str(self.tmp / "state"),
            "ISSUE_LOOP_AGENT_CMD": f"{sys.executable} {agent}",
            "FAKE_WORLD": str(self.world),
            "FAKE_CALLS": str(self.calls),
        }

    def tearDown(self):
        self.gl.close()

    def set_world(self, issues, **kw):
        w = {"issues": issues, "self": "bot"}
        if self.world.exists():
            old = json.loads(self.world.read_text(encoding="utf-8"))
            w["notes"] = old.get("notes", {})
        w.update(kw)
        self.world.write_text(json.dumps(w), encoding="utf-8")

    def cli(self, *args, env=None, wait=True):
        e = {**self.env, **(env or {})}
        cmd = [sys.executable, str(SCRIPT), *args]
        # Windows では Ctrl+Break を送れるように別のプロセスグループで起動する
        group = {"creationflags": subprocess.CREATE_NEW_PROCESS_GROUP} if os.name == "nt" else {}
        if not wait:
            return subprocess.Popen(cmd, env=e, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                    text=True, encoding="utf-8", errors="replace", **group)
        return subprocess.run(cmd, env=e, capture_output=True, text=True, encoding="utf-8",
                              errors="replace", timeout=60, **group)

    def status(self):
        return json.loads(self.cli("status").stdout)

    def agent_calls(self):
        return [json.loads(x) for x in self.calls.read_text(encoding="utf-8").splitlines() if '"start"' in x]



    def event(self, iid: int, updated: str, kinds=("changed",), before=None, name="ev.json") -> Path:
        """api-watch が書くイベントのファイル（handle が使う項目だけ）。"""
        path = self.tmp / name
        rec = next((i for i in json.loads(self.world.read_text(encoding="utf-8"))["issues"] if i["iid"] == iid),
                   make_issue(iid, updated))
        path.write_text(json.dumps({"record": {**rec, "updated_at": updated}, "kinds": list(kinds),
                                    "before": before}), encoding="utf-8")
        return path

    def handle(self, event: Path, *args, env=None, wait=True):
        return self.cli("handle", "--event-file", str(event), *args, env=env, wait=wait)


# ---------------------------------------------------------------------------
# プロンプト
# ---------------------------------------------------------------------------

class PromptTest(unittest.TestCase):
    def test_prompt_keeps_unknown_braces(self):
        cfg = il.Config(api_url="x", project="g/p", token="t")
        item = {"iid": 7, "kind": "new", "changes": ["- 新しい issue"],
                "issue": make_issue(7, "a", title="T")}
        out = il.render_prompt('#{iid} {title} {"k": {unknown}} {changes}', cfg, item)
        self.assertEqual(out, '#7 T {"k": {unknown}} - 新しい issue')

    def test_old_state_file_is_read(self):
        # 以前の版（poll / run）の state.json は、流し終えた版だけを引き継ぐ
        st = il.normalize_state({"baseline_done": True, "seen": {"1": {"version": "v"}}, "queue": {"2": {}},
                                 "dead": {}, "inflight": None})
        self.assertEqual(st["seen"], {"1": {"version": "v"}})
        self.assertNotIn("queue", st)


# ---------------------------------------------------------------------------
# handle 単体（イベントのファイルを直接渡す）
# ---------------------------------------------------------------------------

class HandleTest(Base):
    def setUp(self):
        super().setUp()
        self.set_world([make_issue(1, "2026-11-01T05:00:00Z", labels=["todo"], title="a"),
                        make_issue(2, "2026-11-01T05:00:00Z", title="b")])

    def test_runs_agent_and_marks_seen(self):
        ev = self.event(1, "2026-11-01T05:00:00Z", before={"labels": []})
        r = self.handle(ev)
        self.assertEqual(r.returncode, 0, r.stderr)
        calls = self.agent_calls()
        self.assertEqual(len(calls), 1)
        self.assertIn("#1 a", calls[0]["prompt"])
        self.assertIn("ラベル追加: todo", calls[0]["prompt"])
        self.assertEqual(self.status()["seen"], {"1": "2026-11-01T05:00:00Z"})
        self.assertEqual(self.handle(ev).returncode, 0)            # 同じ変化をもう一度渡されても流さない
        self.assertEqual(len(self.agent_calls()), 1)

    def test_requests_are_bounded(self):
        self.gl.requests.clear()
        self.handle(self.event(1, "2026-11-01T05:00:00Z"))
        # 実行前の取り直し・自分の名前・実行後の取り直し・notes の 4 回
        self.assertEqual(len(self.gl.requests), 4, self.gl.requests)

    def test_forget_makes_it_run_again(self):
        ev = self.event(1, "2026-11-01T05:00:00Z")
        self.handle(ev)
        self.cli("forget", "1")
        self.handle(ev)
        self.assertEqual(len(self.agent_calls()), 2)

    def test_concurrent_handles_start_only_one_session(self):
        procs = [self.handle(self.event(i % 2 + 1, "2026-11-01T05:00:00Z", name=f"ev{i}.json"),
                             env={"FAKE_SLEEP": "2"}, wait=False) for i in range(4)]
        outs = [p.communicate(timeout=60) for p in procs]
        codes = sorted(p.returncode for p in procs)
        self.assertEqual(codes, [0, 4, 4, 4], outs)              # 残りは「あとで再試行」で終わる
        self.assertEqual(len(self.agent_calls()), 1)
        self.assertEqual(sum("別のセッションが実行中" in err for _, err in outs), 3)

    def test_projects_share_one_session_lock_by_default(self):
        a = self.handle(self.event(1, "2026-11-01T05:00:00Z"), env={"FAKE_SLEEP": "2"}, wait=False)
        time.sleep(0.8)
        b = self.handle(self.event(2, "2026-11-01T05:00:00Z", name="ev2.json"), env={"GITLAB_PROJECT": "g/other"})
        a.communicate(timeout=60)
        self.assertEqual(b.returncode, 4)
        self.assertIn("別のセッションが実行中", b.stderr)

    def test_orphan_agent_blocks_next_session(self):
        orphan = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(30)"])
        ev = self.event(1, "2026-11-01T05:00:00Z")
        try:
            lock = Path(self.env["ISSUE_LOOP_STATE_DIR"]) / "session.json"
            lock.parent.mkdir(parents=True, exist_ok=True)
            lock.write_text(json.dumps({"runner_pid": 999999, "child_pid": orphan.pid}), encoding="utf-8")
            r = self.handle(ev)
            self.assertEqual(r.returncode, 4)
            self.assertIn(f"pid {orphan.pid}", r.stderr)
            self.assertEqual(self.agent_calls(), [])
        finally:
            orphan.kill()
            orphan.wait()
        r = self.handle(ev)
        self.assertEqual(len(self.agent_calls()), 1, r.stderr)

    def test_timeout_kills_agent(self):
        t0 = time.time()
        r = self.handle(self.event(1, "2026-11-01T05:00:00Z"), "--delivery-id", "aw_1",
                        env={"FAKE_SLEEP": "30", "ISSUE_LOOP_AGENT_TIMEOUT": "1"})
        self.assertLess(time.time() - t0, 20)
        self.assertEqual(r.returncode, 1)
        self.assertIn("タイムアウト", r.stderr)
        st = self.status()
        self.assertEqual(st["seen"], {})                           # 流し終えていない
        self.assertEqual(st["failing"], {"aw_1": 1})               # 次のプロンプトは 2 回目

    def test_stop_signal_stops_agent(self):
        # POSIX は SIGTERM（Dagu の停止）、Windows は Ctrl+Break で「止めて」と頼む
        p = self.handle(self.event(1, "2026-11-01T05:00:00Z"), env={"FAKE_SLEEP": "30"}, wait=False)
        for _ in range(50):
            if self.agent_calls():
                break
            time.sleep(0.2)
        if os.name == "nt":
            p.send_signal(signal.CTRL_BREAK_EVENT)
        else:
            p.terminate()
        _, err = p.communicate(timeout=30)
        self.assertEqual(p.returncode, 1)
        self.assertIn("中止された", err)
        st = self.status()
        self.assertEqual(st["seen"], {})
        self.assertNotIn("session", st)                          # ロック情報も片付いている

    def test_unlaunchable_agent_is_config_error(self):
        r = self.handle(self.event(1, "2026-11-01T05:00:00Z"), env={"ISSUE_LOOP_AGENT_CMD": "/nonexistent/agent-bin"})
        self.assertEqual(r.returncode, 2, r.stderr)
        self.assertNotIn("session", self.status())

    def test_auth_error_exit_code(self):
        r = self.handle(self.event(1, "2026-11-01T05:00:00Z"), env={"GITLAB_TOKEN": "wrong"})
        self.assertEqual(r.returncode, 3)

    def test_missing_config_exit_code(self):
        r = self.handle(self.event(1, "2026-11-01T05:00:00Z"), env={"GITLAB_PROJECT": ""})
        self.assertEqual(r.returncode, 2)
        self.assertIn("GITLAB_PROJECT", r.stderr)

    def test_unknown_self_username_does_not_loop(self):
        w = json.loads(self.world.read_text(encoding="utf-8"))
        w["self"] = ""                                           # /user からユーザー名が取れない
        self.world.write_text(json.dumps(w), encoding="utf-8")
        self.handle(self.event(1, "2026-11-01T05:00:00Z"), env={"FAKE_MODE": "comment"})
        # エージェント自身のコメントで進んだ版を既読にする（同じ issue を流し続けない）
        self.assertEqual(self.status()["seen"], {"1": "2026-11-01T10:00:00Z"})


class AgentProfileTest(unittest.TestCase):
    def test_repo_profiles_resolve(self):
        tmp = Path(tempfile.mkdtemp())
        pf, of = tmp / "p.md", tmp / "o.txt"
        pf.write_text("hello", encoding="utf-8")
        cfg = il.Config(api_url="x", project="p", token="t", agent="claude")
        argv, via = il.resolve_agent_argv(cfg, pf, of)
        # 先頭は PATH にあれば絶対パスへ解決されるので、名前だけ比べる
        self.assertEqual((Path(argv[0]).stem, argv[1], via), ("claude", "-p", "stdin"))
        cfg.agent = "copilot"
        argv, via = il.resolve_agent_argv(cfg, pf, of)
        self.assertEqual(argv[-2:], ["-p", "hello"])
        cfg.agent = "codex"
        argv, _ = il.resolve_agent_argv(cfg, pf, of)
        self.assertIn(str(of), argv)
        cfg.agent = "ollama"
        with self.assertRaises(il.ConfigError):
            il.resolve_agent_argv(cfg, pf, of)                     # {model} が要る


class PlatformTest(unittest.TestCase):
    def test_pid_alive_does_not_kill(self):
        p = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(30)"])
        try:
            self.assertTrue(il.pid_alive(p.pid))
            self.assertTrue(il.pid_alive(p.pid))             # Windows で os.kill を使うと 1 回目で死ぬ
            self.assertIsNone(p.poll())
        finally:
            p.kill()
            p.wait()
        self.assertFalse(il.pid_alive(p.pid))

    def test_file_lock_is_exclusive_across_handles(self):
        path = Path(tempfile.mkdtemp()) / "x.lock"
        with open(path, "a+") as a, open(path, "a+") as b:
            il.lock_file(a, blocking=False)
            with self.assertRaises(il.LockBusy):
                il.lock_file(b, blocking=False)
            il.unlock_file(a)
            il.lock_file(b, blocking=False)
            il.unlock_file(b)

    @unittest.skipUnless(os.name == "nt", "Windows のコマンド行の分け方")
    def test_split_command_keeps_backslashes_on_windows(self):
        self.assertEqual(il.split_command(r'"C:\Program Files\x\claude.exe" -p --flag'),
                         [r"C:\Program Files\x\claude.exe", "-p", "--flag"])

    def test_split_command_posix(self):
        if os.name == "nt":
            self.skipTest("POSIX のコマンド行の分け方")
        self.assertEqual(il.split_command("claude -p 'a b'"), ["claude", "-p", "a b"])


if __name__ == "__main__":
    unittest.main()


# ---------------------------------------------------------------------------
# api-watch で動かす構成（handle）。本物の api-watch が取得・判定し、command の受け渡し先として
# issue_loop.py handle をその場で 1 件ずつ実行する（dags が書き出す api-watch-issue-agent の 1 本のワークフローと同じ流れ）
# ---------------------------------------------------------------------------

API_WATCH = HERE.parent.parent / "api-watch" / "api_watch.py"


@unittest.skipUnless(API_WATCH.is_file(), "api-watch が無い")
class ApiWatchHandleTest(Base):
    def setUp(self):
        super().setUp()
        self.conn = self.tmp / "connections.json"
        self.conn.write_text(json.dumps(
            {"gitlab": [{"label": "default", "url": "${GITLAB_URL}", "token": "${GITLAB_TOKEN}"}]}), encoding="utf-8")
        self.cfg = self.tmp / "api-watch.json"
        self.cfg.write_text(json.dumps({
            "version": 1,
            "state_dir": str(self.tmp / "aw-state"),
            "sources": {"gitlab-issues": {
                "connection": "gitlab",
                "request": {"url": "{{ conn.url }}/api/v4/projects/{{ env.GITLAB_PROJECT | urlencode }}/issues",
                            "headers": {"PRIVATE-TOKEN": "{{ conn.token }}"},
                            "query": {"state": "opened", "scope": "all", "order_by": "updated_at", "sort": "asc"}},
                "key": "iid",
                "pagination": {"type": "page", "size_param": "per_page", "size": 100},
                "retry": {"attempts": 1}}},
            "rules": {"issue-agent": {
                "source": "gitlab-issues", "id": "iid",
                "watch": ["title", "state", "labels", "assignees[*].username", "milestone.title", "due_date",
                          "user_notes_count", "description"],
                "unordered": ["labels"], "on": ["appeared", "changed"],
                "targets": [{"name": "agent", "command": ["{{ env.ISSUE_LOOP_PYTHON }}", str(SCRIPT), "handle"],
                             "timeout_sec": 60, "order": "none", "retry_backoff_sec": 0}]}},
        }), encoding="utf-8")
        self.env["GITLAB_URL"] = self.gl.url
        self.env["ISSUE_LOOP_PYTHON"] = sys.executable

    def watch(self, env=None):
        e = {**self.env, **(env or {})}
        r = subprocess.run([sys.executable, str(API_WATCH), "--config", str(self.cfg), "--connections",
                            str(self.conn), "run", "--rule", "issue-agent"], env=e, capture_output=True, text=True, encoding="utf-8",
                           errors="replace", timeout=120)
        return r

    def test_new_issue_runs_agent_once(self):
        r = self.watch()
        self.assertEqual(r.returncode, 0, r.stderr)                 # 初回は覚えるだけ
        self.assertEqual(self.agent_calls(), [])
        self.set_world([make_issue(1, "2026-11-01T00:00:00Z"), make_issue(2, "2026-11-01T01:00:00Z")])
        r = self.watch()
        self.assertEqual(r.returncode, 0, r.stderr)
        calls = self.agent_calls()
        self.assertEqual([c["iid"] for c in calls], ["2"])
        self.assertIn("#2 issue 2", calls[0]["prompt"])
        self.assertIn("新しい issue", calls[0]["prompt"])
        self.watch()
        self.assertEqual(len(self.agent_calls()), 1)

    def test_changes_are_listed_and_own_comment_does_not_retrigger(self):
        self.watch()
        self.set_world([make_issue(1, "2026-11-01T05:00:00Z", labels=["todo"], title="renamed")])
        r = self.watch(env={"FAKE_MODE": "comment"})
        self.assertEqual(r.returncode, 0, r.stderr)
        calls = self.agent_calls()
        self.assertEqual(len(calls), 1)
        self.assertIn("ラベル追加: todo", calls[0]["prompt"])
        self.assertIn("タイトル: issue 1 → renamed", calls[0]["prompt"])
        self.assertNotIn("本文", calls[0]["prompt"])
        r = self.watch()                                          # エージェントのコメントを検知するが流さない
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(len(self.agent_calls()), 1)

    def test_comment_by_someone_else_during_run_runs_again(self):
        self.watch()
        self.set_world([make_issue(1, "2026-11-01T05:00:00Z", labels=["todo"])])
        self.watch(env={"FAKE_MODE": "other"})
        self.assertEqual(len(self.agent_calls()), 1)
        r = self.watch()
        self.assertEqual(r.returncode, 0, r.stderr)
        calls = self.agent_calls()
        self.assertEqual(len(calls), 2)
        self.assertIn("コメント +1", calls[1]["prompt"])

    def test_failure_then_retry_runs_once_more_only(self):
        self.watch()
        self.set_world([make_issue(1, "2026-11-01T05:00:00Z", labels=["todo"])])
        event = self.tmp / "ev.json"
        event.write_text(json.dumps({"record": make_issue(1, "2026-11-01T05:00:00Z", labels=["todo"]),
                                     "kinds": ["changed"], "before": {"labels": []}}), encoding="utf-8")
        args = ("handle", "--event-file", str(event), "--delivery-id", "aw_1")
        self.assertEqual(self.cli(*args, env={"FAKE_MODE": "fail"}).returncode, 1)
        r = self.cli(*args)
        self.assertEqual(r.returncode, 0, r.stderr)
        calls = self.agent_calls()
        self.assertEqual(len(calls), 2)
        self.assertIn("ラベル追加: todo", calls[1]["prompt"])
        self.assertEqual(self.cli(*args).returncode, 0)              # 同じ変化をもう一度渡されても流さない
        self.assertEqual(len(self.agent_calls()), 2)

    def test_issue_closed_while_waiting_is_skipped(self):
        event = self.tmp / "ev.json"
        event.write_text(json.dumps({"record": make_issue(1, "2026-11-01T05:00:00Z"), "kinds": ["appeared"]}),
                         encoding="utf-8")
        self.set_world([make_issue(1, "2026-11-01T06:00:00Z", state="closed")])
        r = self.cli("handle", "--event-file", str(event))
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(self.agent_calls(), [])

    def test_busy_session_asks_for_retry(self):
        event = self.tmp / "ev.json"
        event.write_text(json.dumps({"record": make_issue(1, "2026-11-01T05:00:00Z"), "kinds": ["appeared"]}),
                         encoding="utf-8")
        lock = self.tmp / "state" / "session.lock"
        lock.parent.mkdir(parents=True, exist_ok=True)
        with open(lock, "a+") as f:
            il.lock_file(f, blocking=False)
            try:
                r = self.cli("handle", "--event-file", str(event))
            finally:
                il.unlock_file(f)
        self.assertEqual(r.returncode, 4, r.stderr)
        self.assertEqual(self.agent_calls(), [])

    def test_missing_record_is_config_error(self):
        event = self.tmp / "ev.json"
        event.write_text(json.dumps({"kinds": ["changed"]}), encoding="utf-8")
        self.assertEqual(self.cli("handle", "--event-file", str(event)).returncode, 2)
