"""dagu-issue-loop のテスト（標準ライブラリだけ。`python -m unittest discover -s test`）。

GitLab は手元の HTTP サーバで偽装し、エージェント CLI は小さな Python スクリプトで偽装する。
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
from unittest import mock
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
            "ISSUE_LOOP_RETRY_BACKOFF": "0",
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


# ---------------------------------------------------------------------------
# 変化の検知と重複制御（純粋関数）
# ---------------------------------------------------------------------------

class PollLogicTest(unittest.TestCase):
    def test_first_poll_is_baseline(self):
        st = il.empty_state()
        c = il.poll_into_state(st, [make_issue(1, "a"), make_issue(2, "a")], complete=True, baseline=True, now=1)
        self.assertEqual(c["baseline"], 2)
        self.assertEqual(st["queue"], {})

    def test_no_baseline_queues_everything(self):
        st = il.empty_state()
        il.poll_into_state(st, [make_issue(1, "a")], complete=True, baseline=False, now=1)
        self.assertEqual(st["queue"]["1"]["kind"], "new")

    def test_new_and_changed_are_queued_once(self):
        st = il.empty_state()
        il.poll_into_state(st, [make_issue(1, "a")], complete=True, baseline=True, now=1)
        issues = [make_issue(1, "b", labels=["bug"]), make_issue(2, "a")]
        c = il.poll_into_state(st, issues, complete=True, baseline=True, now=2)
        self.assertEqual(c["queued"], 2)
        self.assertIn("- ラベル追加: bug", st["queue"]["1"]["changes"])
        # 同じ版をもう一度見ても積み増さない
        c = il.poll_into_state(st, issues, complete=True, baseline=True, now=3)
        self.assertEqual(c["queued"] + c["coalesced"], 0)
        self.assertEqual(len(st["queue"]), 2)

    def test_changes_are_coalesced_per_issue(self):
        st = il.empty_state()
        il.poll_into_state(st, [make_issue(1, "a")], complete=True, baseline=True, now=1)
        il.poll_into_state(st, [make_issue(1, "b", labels=["x"])], complete=True, baseline=True, now=2)
        c = il.poll_into_state(st, [make_issue(1, "c", labels=["x", "y"], user_notes_count=2)],
                               complete=True, baseline=True, now=3)
        self.assertEqual(c["coalesced"], 1)
        q = st["queue"]["1"]
        self.assertEqual(q["version"], "c")
        self.assertEqual(q["enqueued_at"], 2)        # 順番は最初に積んだときのまま
        self.assertIn("- ラベル追加: x, y", q["changes"])   # 差分は既読からの累積
        self.assertIn("- コメント +2", q["changes"])

    def test_timestamp_only_update_is_not_a_change(self):
        st = il.empty_state()
        il.poll_into_state(st, [make_issue(1, "a")], complete=True, baseline=True, now=1)
        c = il.poll_into_state(st, [make_issue(1, "b")], complete=True, baseline=True, now=2)
        self.assertEqual(c["queued"], 0)
        self.assertEqual(st["seen"]["1"]["version"], "b")

    def test_inflight_version_is_not_requeued(self):
        st = il.empty_state()
        il.poll_into_state(st, [make_issue(1, "a")], complete=True, baseline=True, now=1)
        il.poll_into_state(st, [make_issue(1, "b", title="t")], complete=True, baseline=True, now=2)
        st["inflight"] = {"key": "1", "version": "b"}
        st["queue"].pop("1")
        c = il.poll_into_state(st, [make_issue(1, "b", title="t")], complete=True, baseline=True, now=3)
        self.assertEqual(c["queued"], 0)

    def test_issue_leaving_filter_is_dropped_only_when_list_complete(self):
        st = il.empty_state()
        il.poll_into_state(st, [], complete=True, baseline=True, now=1)
        il.poll_into_state(st, [make_issue(1, "a")], complete=True, baseline=True, now=2)
        il.poll_into_state(st, [], complete=False, baseline=True, now=3)
        self.assertIn("1", st["queue"])
        c = il.poll_into_state(st, [], complete=True, baseline=True, now=4)
        self.assertEqual(c["dropped"], 1)

    def test_dead_issue_revives_on_new_change(self):
        st = il.empty_state()
        cfg = il.Config(api_url="x", project="p", token="t", max_attempts=1)
        il.poll_into_state(st, [], complete=True, baseline=True, now=1)
        il.poll_into_state(st, [make_issue(1, "a")], complete=True, baseline=True, now=2)
        self.assertEqual(il.settle_failure(st, st["queue"]["1"], cfg, 3, "x"), "dead")
        il.poll_into_state(st, [make_issue(1, "a")], complete=True, baseline=True, now=4)
        self.assertNotIn("1", st["queue"])
        il.poll_into_state(st, [make_issue(1, "b", user_notes_count=1)], complete=True, baseline=True, now=5)
        self.assertIn("1", st["queue"])
        self.assertNotIn("1", st["dead"])

    def test_prompt_keeps_unknown_braces(self):
        cfg = il.Config(api_url="x", project="g/p", token="t")
        item = {"iid": 7, "kind": "new", "changes": ["- 新しい issue"],
                "issue": make_issue(7, "a", title="T")}
        out = il.render_prompt('#{iid} {title} {"k": {unknown}} {changes}', cfg, item)
        self.assertEqual(out, '#7 T {"k": {unknown}} - 新しい issue')


# ---------------------------------------------------------------------------
# poll → run を通しで（偽 GitLab + 偽エージェント）
# ---------------------------------------------------------------------------

class EndToEndTest(Base):
    def test_poll_then_run_marks_seen(self):
        self.assertEqual(self.cli("poll").returncode, 0)            # baseline
        self.set_world([make_issue(1, "2026-11-01T00:00:00Z"), make_issue(2, "2026-11-01T01:00:00Z")])
        self.assertEqual(self.cli("poll").returncode, 0)
        self.assertEqual([q["iid"] for q in self.status()["queue"]], [2])
        r = self.cli("run")
        self.assertEqual(r.returncode, 0, r.stderr)
        calls = self.agent_calls()
        self.assertEqual(len(calls), 1)
        self.assertIn("#2 issue 2", calls[0]["prompt"])
        self.assertEqual(self.status()["queue"], [])
        self.cli("run")
        self.assertEqual(len(self.agent_calls()), 1)               # 空なら起こさない

    def test_agent_own_comment_does_not_retrigger(self):
        self.cli("poll")
        self.set_world([make_issue(1, "2026-11-01T05:00:00Z", labels=["todo"])])
        self.cli("poll")
        r = self.cli("run", env={"FAKE_MODE": "comment"})
        self.assertEqual(r.returncode, 0, r.stderr)
        self.cli("poll")                                         # エージェントのコメントを見る
        self.assertEqual(self.status()["queue"], [])
        self.cli("run")
        self.assertEqual(len(self.agent_calls()), 1)

    def test_comment_by_someone_else_during_run_requeues(self):
        self.cli("poll")
        self.set_world([make_issue(1, "2026-11-01T05:00:00Z", labels=["todo"])])
        self.cli("poll")
        # 実行中に他人がコメントした体（偽エージェントの代わりに world を先に書いておく）
        w = json.loads(self.world.read_text(encoding="utf-8"))
        w["notes"] = {"1": [{"created_at": "2999-01-01T00:00:00Z", "author": {"username": "alice"}}]}
        w["issues"][0]["user_notes_count"] = 1
        w["issues"][0]["updated_at"] = "2026-11-01T06:00:00Z"
        self.world.write_text(json.dumps(w), encoding="utf-8")
        self.cli("run")
        q = self.status()["queue"]
        self.assertEqual([x["iid"] for x in q], [1])
        self.assertEqual(q[0]["version"], "2026-11-01T06:00:00Z")

    def test_failure_retries_then_gives_up(self):
        self.cli("poll")
        self.set_world([make_issue(1, "2026-11-01T05:00:00Z", title="new")])
        self.cli("poll")
        env = {"FAKE_MODE": "fail", "ISSUE_LOOP_MAX_ATTEMPTS": "2"}
        self.assertEqual(self.cli("run", env=env).returncode, 1)
        self.assertEqual(self.status()["queue"][0]["attempts"], 1)
        self.assertEqual(self.cli("run", env=env).returncode, 1)
        st = self.status()
        self.assertEqual(st["queue"], [])
        self.assertEqual(st["dead"][0]["iid"], 1)
        self.cli("poll")                                         # 同じ版なら蘇らない
        self.assertEqual(self.status()["queue"], [])
        self.cli("forget", "1")
        self.cli("poll")
        self.assertEqual([q["kind"] for q in self.status()["queue"]], ["new"])

    def test_run_with_empty_queue_does_not_touch_gitlab(self):
        # agent の DAG は毎分起動する。待ち行列が空なら GitLab へは 1 回も問い合わせない
        self.cli("poll")
        self.gl.requests.clear()
        for _ in range(3):
            self.assertEqual(self.cli("run").returncode, 0)
        self.assertEqual(self.gl.requests, [])

    def test_run_requests_are_bounded_per_session(self):
        self.cli("poll")
        self.set_world([make_issue(1, "2026-11-01T05:00:00Z", title="x")])
        self.cli("poll")
        self.gl.requests.clear()
        self.cli("run")
        # 実行後の確認だけ: 自分の名前・issue の取り直し・notes の 3 回
        self.assertEqual(len(self.gl.requests), 3, self.gl.requests)

    def test_poll_jitter_waits_before_fetching(self):
        self.cli("poll")
        self.gl.requests.clear()
        t0 = time.time()
        r = self.cli("poll", "--jitter", "1")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("秒ずらします", r.stderr)
        self.assertLess(time.time() - t0, 15)
        self.assertEqual(len(self.gl.requests), 1)

    def test_auth_error_exit_code(self):
        r = self.cli("poll", env={"GITLAB_TOKEN": "wrong"})
        self.assertEqual(r.returncode, 3)

    def test_missing_config_exit_code(self):
        r = self.cli("run", env={"GITLAB_PROJECT": ""})
        self.assertEqual(r.returncode, 2)
        self.assertIn("GITLAB_PROJECT", r.stderr)


# ---------------------------------------------------------------------------
# 1 セッションだけを保証する
# ---------------------------------------------------------------------------

class SingleSessionTest(Base):
    def queue_two(self):
        self.cli("poll")
        self.set_world([make_issue(1, "2026-11-01T05:00:00Z", title="a"),
                        make_issue(2, "2026-11-01T05:00:00Z", title="b")])
        self.cli("poll")

    def test_concurrent_runs_start_only_one_session(self):
        self.queue_two()
        procs = [self.cli("run", env={"FAKE_SLEEP": "2"}, wait=False) for _ in range(4)]
        outs = [p.communicate(timeout=60) for p in procs]
        self.assertTrue(all(p.returncode == 0 for p in procs), outs)
        self.assertEqual(len(self.agent_calls()), 1)
        self.assertEqual(sum("別のセッションが実行中" in err for _, err in outs), 3)

    def test_projects_share_one_session_lock_by_default(self):
        self.queue_two()
        other = {"GITLAB_PROJECT": "g/other"}
        self.cli("poll", env=other)
        a = self.cli("run", env={"FAKE_SLEEP": "2"}, wait=False)
        time.sleep(0.8)
        b = self.cli("run", env=other)
        a.communicate(timeout=60)
        self.assertIn("別のセッションが実行中", b.stderr)

    def test_orphan_agent_blocks_next_session(self):
        self.queue_two()
        orphan = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(30)"])
        try:
            lock = Path(self.env["ISSUE_LOOP_STATE_DIR"]) / "session.json"
            lock.parent.mkdir(parents=True, exist_ok=True)
            lock.write_text(json.dumps({"runner_pid": 999999, "child_pid": orphan.pid}), encoding="utf-8")
            r = self.cli("run")
            self.assertIn(f"pid {orphan.pid}", r.stderr)
            self.assertEqual(self.agent_calls(), [])
        finally:
            orphan.kill()
            orphan.wait()
        r = self.cli("run")
        self.assertEqual(len(self.agent_calls()), 1, r.stderr)

    def test_timeout_kills_agent_and_counts_failure(self):
        self.queue_two()
        t0 = time.time()
        r = self.cli("run", env={"FAKE_SLEEP": "30", "ISSUE_LOOP_AGENT_TIMEOUT": "1"})
        self.assertLess(time.time() - t0, 20)
        self.assertEqual(r.returncode, 1)
        self.assertIn("タイムアウト", self.status()["queue"][0]["last_error"])
        self.assertIsNone(self.status()["inflight"])

    def test_stop_signal_stops_agent_and_records(self):
        # POSIX は SIGTERM（Dagu の停止）、Windows は Ctrl+Break で「止めて」と頼む
        self.queue_two()
        p = self.cli("run", env={"FAKE_SLEEP": "30"}, wait=False)
        for _ in range(50):
            if self.agent_calls():
                break
            time.sleep(0.2)
        if os.name == "nt":
            p.send_signal(signal.CTRL_BREAK_EVENT)
        else:
            p.terminate()
        p.communicate(timeout=30)
        self.assertEqual(p.returncode, 1)
        st = self.status()
        self.assertIsNone(st["inflight"])
        self.assertEqual(st["queue"][0]["last_error"], "中止された")
        self.assertNotIn("session", st)                         # ロック情報も片付いている

    def test_stale_inflight_is_recovered(self):
        self.queue_two()
        keys = {k: v for k, v in self.env.items() if k.startswith(("GITLAB", "ISSUE_LOOP"))}
        with mock.patch.dict(os.environ, keys):
            cfg = il.build_config(il.build_parser().parse_args(["status"]), need_gitlab=False)
        with il.locked_state(cfg) as st:
            st["inflight"] = {"key": "1", "version": st["queue"]["1"]["version"], "started_at": 0, "pid": 1}
        r = self.cli("run")
        self.assertIn("失敗として数えます", r.stderr)
        self.assertEqual(len(self.agent_calls()), 1)


class JitterTest(unittest.TestCase):
    def test_delay_is_within_range_and_off_by_default(self):
        cfg = il.Config(api_url="x", project="p", token="t", poll_jitter=900)

        class Boom(Exception):
            pass

        delays = []

        def fake_sleep(sec):
            delays.append(sec)
            raise Boom()   # 待ったあとの取得までは進めない

        for _ in range(50):
            with self.assertRaises(Boom):
                il.cmd_poll(cfg, None, sleep=fake_sleep)
        self.assertTrue(all(0 <= d <= 900 for d in delays))
        self.assertGreater(len(set(round(d) for d in delays)), 1)   # 毎回同じ値ではない

        cfg.poll_jitter = 0
        calls = []

        class FakeGL:
            def list_issues(self, **kw):
                calls.append(kw)
                return [], True

        with tempfile.TemporaryDirectory() as d:
            cfg.state_dir = Path(d)
            il.cmd_poll(cfg, FakeGL(), sleep=fake_sleep)   # 0 なら sleep を呼ばない
        self.assertEqual(len(calls), 1)


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
