"""通知の文言、配信台帳、change-watch とのワークフローを検証する。"""
from contextlib import closing, redirect_stderr, redirect_stdout
import importlib.util
import io
import json
import os
from pathlib import Path
import re
import shutil
import sqlite3
import subprocess
import sys
import tempfile
import time
from types import ModuleType
import unittest
from unittest.mock import patch

AGENT = Path(__file__).resolve().parents[1]
CHANGE_WATCH = AGENT.parents[1]
sys.path[:0] = [str(AGENT), str(CHANGE_WATCH / "test"),
                str(AGENT.parent / "gitlab-issue-agent" / "test")]

import dagrun
from test_gitlab_issue import FakeGitLab, make_issue

import gitlab_notify as notify


def change(kind="appeared", **record):
    return {"kind": kind, "key": "12", "record": make_issue(12, **record), "changes": []}


def changed(before, **record):
    result = dict(change("changed"), record={**before, **record})
    return dict(result, before=before, changes=["ignored"])


def rendered(data, **settings):
    return [message for _, message, _ in notify.messages("my-api", settings, data)]


class FormattingTests(unittest.TestCase):
    def test_messages_match_issue_mailbox(self):
        root = Path(self.enterContext(tempfile.TemporaryDirectory()))
        tmux = root / "tmux"
        tmux.write_text("#!/bin/sh\nexit 0\n")
        tmux.chmod(0o755)
        path = CHANGE_WATCH.parent / "issue-mailbox" / "issue-mailbox.py"
        try:
            spec = importlib.util.spec_from_file_location("issue_mailbox", path)
            mailbox = importlib.util.module_from_spec(spec)
            with patch.dict(os.environ, PATH=os.pathsep.join([str(root), os.environ.get("PATH", "")])), \
                    patch.dict(sys.modules, {name: ModuleType(name) for name in ("requests", "yaml")}):
                spec.loader.exec_module(mailbox)
        except (Exception, SystemExit) as exc:
            self.skipTest(f"issue-mailbox を読み込めない: {exc}")

        watch = dict.fromkeys(notify.WATCH, True)
        issue = make_issue(12, labels=["triage", "bug"], assignee={"username": "sato"})
        cases = [
            ("appeared", dict(change(), record=issue), 1),
            ("state", changed(issue, state="closed"), 1),
            ("unassign", changed(issue, assignee=None), 1),
            ("assign", changed({**issue, "assignee": None}, assignee={"username": "sato"}), 1),
            ("labels", changed(issue, labels=["feature", "bug"]), 1),
            ("combined", changed(issue, state="closed", title="新しい題",
                                 labels=["feature"], assignee={"username": "ito"}), 4),
            ("removed", dict(change("removed"), record=issue), 1),
        ]
        for name, data, count in cases:
            with self.subTest(case=name):
                state_dir = root / name
                before = [] if data["kind"] == "appeared" else [data.get("before", data["record"])]
                current = [] if data["kind"] == "removed" else [data["record"]]
                filter_state = "opened" if data["kind"] != "changed" else "all"
                mailbox.detect_changes("my-api", state_dir, before, watch, filter_state)
                expected = mailbox.detect_changes("my-api", state_dir, current, watch, filter_state)
                self.assertEqual(len(expected), count)
                self.assertEqual(rendered(data, watch=watch), [f"[my-api] {msg}" for msg in expected])

    def test_appeared_and_removed(self):
        data = change(title="ログインできない", labels=["triage", "bug"], assignee={"username": "sato"})
        self.assertEqual(rendered(data), ["[my-api] [NEW] #12 ログインできない [bug, triage] @sato"])
        self.assertEqual(rendered(change()), ["[my-api] [NEW] #12 issue 12"])
        self.assertEqual(rendered(dict(data, kind="removed")), ["[my-api] [CLOSED] #12 ログインできない"])
        self.assertEqual(rendered(dict(data, kind="removed"), removed_as_closed=False), [])
        self.assertEqual(rendered(dict(data, kind="removed"), watch={"state_changes": False}), [])
        self.assertEqual(rendered(data, watch={"new_issues": False}), [])

    def test_changed_fields_individually_and_in_order(self):
        watch = {key: True for key in notify.WATCH}
        before = make_issue(12, labels=["x", "w"], assignee=None)
        updates = [
            ("state", "closed", "[my-api] [UPD] #12 state:opened→closed  issue 12"),
            ("title", "新しい題", "[my-api] [UPD] #12 title changed  新しい題"),
            ("labels", ["z", "a"], "[my-api] [UPD] #12 labels:+a,z -w,x  issue 12"),
            ("assignee", {"username": "sato"}, "[my-api] [UPD] #12 assignee:(未割り当て)→sato  issue 12"),
        ]
        for field, value, expected in updates:
            with self.subTest(field=field):
                self.assertEqual(rendered(changed(before, **{field: value}), watch=watch), [expected])
        data = changed(before, **{field: value for field, value, _ in updates})
        self.assertEqual(rendered(data, watch=watch), [
            "[my-api] [UPD] #12 state:opened→closed  新しい題",
            "[my-api] [UPD] #12 title changed  新しい題",
            "[my-api] [UPD] #12 labels:+a,z -w,x  新しい題",
            "[my-api] [UPD] #12 assignee:(未割り当て)→sato  新しい題",
        ])
        self.assertEqual(rendered(data, watch={key: False for key in watch}), [])
        self.assertEqual(rendered(data), [
            "[my-api] [UPD] #12 state:opened→closed  新しい題",
            "[my-api] [UPD] #12 assignee:(未割り当て)→sato  新しい題",
        ])

    def test_label_order_and_one_sided_deltas(self):
        before = make_issue(12, labels=["b", "a"])
        watch = {"label_changes": True}
        self.assertEqual(rendered(changed(before, labels=["a", "b"]), watch=watch), [])
        self.assertEqual(rendered(changed(before, labels=[]), watch=watch),
                         ["[my-api] [UPD] #12 labels:-a,b  issue 12"])
        self.assertEqual(rendered(changed(before, labels=["a", "b", "c"]), watch=watch),
                         ["[my-api] [UPD] #12 labels:+c  issue 12"])

    def test_prompt_expands_once_and_replaces_control_characters(self):
        event, message, fields = next(notify.messages("my-api", {}, change(title="{iid}\r\n\x1btest")))
        row = dict(ts="2026-11-20 09:05:12", event=event, message=message, fields=json.dumps(fields))
        self.assertEqual(notify.prompt_text({}, row), "2026-11-20 09:05:12  [my-api] [NEW] #12 {iid} test")
        self.assertEqual(notify.prompt_text({"template": "{message}", "templates": {"new": "{title} #{iid}"}}, row),
                         "{iid} test #12")
        row["message"] = "[my-api] [NEW] #12 issue 12"
        self.assertEqual(notify.prompt_text({}, row), notify.line(row))


class OutboxTests(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.root = Path(directory.name)
        self.config_path = self.root / "notify.json"
        self.db_path = self.root / "outbox.db"
        self.output, self.errors = io.StringIO(), io.StringIO()
        self.enterContext(redirect_stdout(self.output))
        self.enterContext(redirect_stderr(self.errors))
        self.enterContext(patch.dict(os.environ, GITLAB_NOTIFY_CONFIG=str(self.config_path), GITLAB_TOKEN="top-secret"))
        self.clock = self.enterContext(patch.object(notify.time, "time", return_value=1763629520))
        self.config = {"outbox": str(self.db_path), "repos": {"my-api": {}}, "channels": [
            {"id": name, "type": "log", "path": str(self.root / f"{name}.log")} for name in ("A", "B")]}
        self.write_config()
        self.attempts, self.delivered = [], []
        self.failing_channels = set()
        self.enterContext(patch.dict(notify.SEND, log=self.send))

    def write_config(self):
        self.config_path.write_text(json.dumps(self.config), encoding="utf-8")

    def send(self, channel, row):
        name = channel["id"]
        self.attempts.append((name, row["rkey"]))
        # 配信前に台帳が別の接続から読めることを確認する。
        self.assertIn((name, row["rkey"]), [(r["channel"], r["rkey"]) for r in self.rows()])
        if name in self.failing_channels:
            raise OSError("tmux indisponible top-secret\nretry later")
        self.delivered.append((name, notify.line(row)))

    def rows(self):
        with closing(sqlite3.connect(self.db_path)) as db:
            db.row_factory = sqlite3.Row
            return [dict(r) for r in db.execute("SELECT * FROM outbox ORDER BY seq")]

    def enqueue(self, data):
        return notify.main(["notify", "--repo", "my-api", json.dumps(data)])

    def advance(self, seconds):
        self.clock.return_value += seconds

    def test_partial_failure_retry_and_timestamp(self):
        self.failing_channels.add("B")
        self.assertEqual(self.enqueue(change()), 0)
        a, b = self.rows()
        self.assertEqual((a["status"], b["status"]), ("sent", "pending"))
        self.assertEqual(b["next_attempt_at"] - self.clock.return_value, 60)
        self.assertIn("B key=12: tmux indisponible *** retry later", self.errors.getvalue())
        self.assertNotIn("top-secret", self.errors.getvalue())
        self.assertEqual(notify.main(["status", "--json"]), 0)
        self.assertEqual(json.loads(self.output.getvalue().splitlines()[-1]), [{
            "repo": "my-api", "channel": "B", "pending": 1, "dead": 0,
            "oldest_pending": b["ts"], "last_error": "tmux indisponible *** retry later",
        }])
        self.failing_channels.clear()
        self.advance(60)
        self.assertEqual(notify.main(["retry"]), 0)
        self.assertEqual(notify.main(["retry"]), 0)
        self.assertEqual(self.delivered, [("A", notify.line(a)), ("B", notify.line(b))])
        self.assertRegex(self.delivered[0][1], r"^\d{4}-\d\d-\d\d \d\d:\d\d:\d\d  \[my-api\]")

    def test_same_change_and_added_message(self):
        before = make_issue(12)
        first = changed(before, state="closed")
        self.assertEqual(self.enqueue(first), 0)
        self.assertEqual(self.enqueue(first), 0)
        self.assertEqual(len(self.rows()), 2)
        self.assertEqual(len(self.delivered), 2)
        self.assertEqual(self.enqueue(changed(before, state="closed", assignee={"username": "sato"})), 0)
        self.assertEqual([row["event"] for row in self.rows()], ["state", "state", "assignee", "assignee"])
        self.assertEqual(len(self.delivered), 4)
        later = dict(before, updated_at="2026-11-21T10:00:00Z")
        self.assertEqual(self.enqueue(changed(later, state="closed")), 0)
        self.assertEqual(len(self.rows()), 6)
        self.assertTrue(all(re.fullmatch(r"[a-f0-9]{32}", row["id"]) for row in self.rows()))

    def test_send_allows_sqlite_writer_but_excludes_other_delivery(self):
        with closing(notify.open_outbox(self.db_path)) as competing:
            competing.execute("PRAGMA busy_timeout = 0")
            _, _, channels = notify.load_config()

            def send(channel, row):
                with competing:
                    competing.execute("BEGIN IMMEDIATE")
                with patch.dict(notify.SEND, log=lambda *_: self.fail("競合する配信が実行された")):
                    self.assertFalse(notify.deliver(competing, channel["id"], channel))
                pending = next(r for r in self.rows() if r["id"] == row["id"])
                self.assertEqual(pending["status"], "pending")

            with patch.dict(notify.SEND, log=send):
                self.assertEqual(self.enqueue(change()), 0)
            self.assertEqual([row["status"] for row in self.rows()], ["sent", "sent"])

    def test_unexpected_send_exception_is_recorded(self):
        def send(channel, row):
            raise RuntimeError("unexpected delivery failure")

        with patch.dict(notify.SEND, log=send):
            self.assertEqual(self.enqueue(change()), 0)
        for row in self.rows():
            self.assertEqual((row["status"], row["attempts"]), ("pending", 1))
            self.assertEqual(row["last_error"], "unexpected delivery failure")
            self.assertEqual(row["next_attempt_at"] - row["last_attempt_at"], 60)

    def test_notify_succeeds_if_delivery_raises_after_enqueue(self):
        with patch.object(notify, "deliver", side_effect=RuntimeError("top-secret\nlock error")):
            self.assertEqual(self.enqueue(change()), 0)
        self.assertEqual([row["status"] for row in self.rows()], ["pending", "pending"])
        self.assertIn("A key=12: *** lock error", self.errors.getvalue())
        self.advance(1)
        self.assertEqual(notify.main(["retry"]), 0)
        self.assertEqual([row["status"] for row in self.rows()], ["sent", "sent"])

    def test_routing_and_stdin(self):
        self.config["channels"][0]["events"] = ["closed"]
        self.config["channels"][1]["repos"] = ["other"]
        self.config["channels"].append({"id": "C", "type": "log", "enabled": False})
        self.write_config()
        self.assertEqual(self.enqueue(change()), 0)
        self.assertEqual(self.rows(), [])
        with patch("sys.stdin", io.StringIO(json.dumps(change("removed")))):
            self.assertEqual(notify.main(["notify", "--repo", "my-api", "-"]), 0)
        self.assertEqual(self.attempts, [("A", "12")])

    def test_invalid_input_config_and_outbox(self):
        self.assertEqual(notify.main(["notify", "--repo", "my-api", "{"]), 1)
        self.assertEqual(notify.main(["notify", "--repo", "unknown", json.dumps(change())]), 1)
        self.config["channels"].append({"id": "prompt", "type": "prompt", "target": "main", "template": "{unknown}"})
        self.write_config()
        self.assertEqual(self.enqueue(change()), 1)
        self.assertIn("チャネル prompt", self.errors.getvalue())
        self.config["channels"].pop()
        self.db_path.unlink()
        self.db_path.mkdir()
        self.assertEqual(self.enqueue(change()), 1)
        self.assertEqual(self.attempts, [])

    def test_partial_insert_is_rolled_back_before_delivery(self):
        with closing(notify.open_outbox(self.db_path)) as db:
            db.execute("""
                CREATE TRIGGER reject_b BEFORE INSERT ON outbox WHEN NEW.channel = 'B'
                BEGIN
                    SELECT RAISE(ABORT, 'test insert failure');
                END
            """)
        self.assertEqual(self.enqueue(change()), 1)
        self.assertEqual(self.rows(), [])
        self.assertEqual(self.attempts, [])

    def test_order_backoff_expiry_and_cleanup(self):
        self.config["channels"][0]["max_age_minutes"] = 4
        self.write_config()
        self.failing_channels.add("A")
        self.assertEqual(self.enqueue(change()), 0)
        self.advance(30)
        second = dict(change(), key="13")
        self.assertEqual(self.enqueue(second), 0)
        self.assertEqual(self.attempts, [("A", "12"), ("B", "12"), ("B", "13")])
        self.assertEqual(notify.main(["retry"]), 0)
        self.advance(30)
        self.assertEqual(notify.main(["retry"]), 1)
        self.assertEqual(self.attempts[-1], ("A", "12"))
        self.assertEqual(self.rows()[0]["next_attempt_at"] - self.clock.return_value, 120)
        self.failing_channels.clear()
        self.advance(180)
        self.assertEqual(notify.main(["retry"]), 0)
        self.assertEqual(self.attempts[-1], ("A", "13"))
        self.assertEqual(self.rows()[0]["status"], "dead")
        self.advance(7 * 86400)
        self.assertEqual(notify.main(["retry"]), 0)
        self.assertEqual([row["status"] for row in self.rows()], ["dead"])
        self.advance(23 * 86400)
        self.assertEqual(notify.main(["retry"]), 0)
        self.assertEqual(self.rows(), [])

    def test_disabled_and_removed_channel_expiry(self):
        for channel in self.config["channels"]:
            channel["max_age_minutes"] = 2
        self.write_config()
        self.failing_channels.update(["A", "B"])
        self.assertEqual(self.enqueue(change()), 0)
        self.config["channels"].pop()
        self.config["channels"][0]["enabled"] = False
        self.write_config()
        self.advance(60)
        self.assertEqual(notify.main(["retry"]), 0)
        self.assertEqual(len(self.attempts), 2)
        self.advance(60)
        self.assertEqual(notify.main(["retry"]), 0)
        self.assertEqual([r["status"] for r in self.rows()], ["dead", "dead"])

    def test_exponential_backoff_reaches_one_hour_cap(self):
        self.failing_channels.add("A")
        self.assertEqual(self.enqueue(change()), 0)
        for expected in [60, 120, 240, 480, 960, 1920, 3600, 3600]:
            self.assertEqual(self.rows()[0]["next_attempt_at"] - self.clock.return_value, expected)
            self.advance(expected)
            self.assertEqual(notify.main(["retry"]), 1)


class ChannelTests(unittest.TestCase):
    def test_tmux_arguments_and_terminal_semicolon(self):
        row = {"ts": "2026-11-20 09:05:12", "message": "[my-api] [NEW] #12 a;", "event": "new", "fields": "{}"}
        with patch.object(notify.subprocess, "run", return_value=subprocess.CompletedProcess([], 0)) as run:
            for mode, suffix in (("tmux", []), ("prompt", [";", "send-keys", "-t", "main:0", "Enter"])):
                with self.subTest(mode=mode):
                    notify.send_tmux({"type": mode, "target": "main:0"}, row)
                    run.assert_called_with(["tmux", "send-keys", "-t", "main:0", "-l", "--",
                                            r"2026-11-20 09:05:12  [my-api] [NEW] #12 a\;", *suffix],
                                           capture_output=True, text=True, timeout=10)
            run.return_value = subprocess.CompletedProcess([], 1, stderr="pane not found")
            with self.assertRaisesRegex(ValueError, "pane not found"):
                notify.send_tmux({"type": "tmux", "target": "main:0"}, row)

    def test_hub_sends_code_block_and_rejects_other_instance(self):
        row = {"message": "[my-api] [NEW] #12 @sato ```\n /close",
               "fields": json.dumps({"url": "https://gitlab.example/g/p/-/issues/12"})}
        with patch.dict(os.environ, GITLAB_URL="https://gitlab.example", GITLAB_TOKEN="top-secret",
                        GITLAB_HUB_ISSUE_URL="https://gitlab.example/g/hub/-/issues/4"), \
                patch.object(notify, "build_opener") as opener:
            notify.send_hub({}, row)
            request = opener.return_value.open.call_args.args[0]
            self.assertEqual(request.full_url, "https://gitlab.example/api/v4/projects/g%2Fhub/issues/4/notes")
            self.assertEqual(request.get_header("Private-token"), "top-secret")
            self.assertEqual(json.loads(request.data)["body"],
                             "【通知】\n\n````\n[my-api] [NEW] #12 @sato ```\n \\/close\n````\n"
                             "https://gitlab.example/g/p/-/issues/12")
            self.assertEqual(opener.return_value.open.call_args.kwargs, {"timeout": 30})
            with self.assertRaisesRegex(ValueError, "同じ GitLab"):
                notify.send_hub({"issue_url": "https://gitlab.example.evil/g/hub/-/issues/4"}, row)
            self.assertEqual(opener.return_value.open.call_count, 1)

    def test_example_settings(self):
        with patch.dict(os.environ, GITLAB_NOTIFY_CONFIG=str(AGENT / "gitlab-notify.json.example")):
            _, repos, channels = notify.load_config()
        self.assertEqual(set(repos), {"my-api"})
        self.assertEqual([c["type"] for c in channels.values() if c["enabled"]], ["log", "tmux"])


@unittest.skipUnless(shutil.which("tmux"), "tmux が見つからない")
class TmuxIntegrationTests(unittest.TestCase):
    def setUp(self):
        directory = self.enterContext(tempfile.TemporaryDirectory())
        self.enterContext(patch.dict(os.environ, TMUX_TMPDIR=directory))
        self.addCleanup(self.tmux, "kill-server")
        result = self.tmux(
            "new-session", "-d", "-s", "notify", "-x", "120", "-y", "24",
            "env PS1='notify-ready> ' PROMPT_COMMAND= INPUTRC=/dev/null bash --noprofile --norc",
        )
        if result.returncode and ("Operation not permitted" in result.stderr
                                  or "Permission denied" in result.stderr):
            self.skipTest(f"この環境では tmux の専用ソケットを開けない: {result.stderr.strip()}")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.target = "notify:0.0"
        self.wait_for_line(lambda line: line == "notify-ready>")

    def tmux(self, *args):
        return subprocess.run(["tmux", "-L", "gitlab-notify-test", "-f", "/dev/null", *args],
                              capture_output=True, text=True, timeout=10)

    def wait_for_line(self, matches):
        deadline = time.monotonic() + 5
        while True:
            result = self.tmux("capture-pane", "-p", "-t", self.target)
            self.assertEqual(result.returncode, 0, result.stderr)
            if any(matches(line.rstrip()) for line in result.stdout.splitlines()):
                return
            if time.monotonic() >= deadline:
                self.fail(f"ペインに期待した行が現れなかった: {result.stdout!r}")
            time.sleep(0.05)

    def send(self, mode, message):
        row = {"ts": "2026-11-20 09:05:12", "message": message, "event": "new", "fields": "{}"}
        with patch.object(notify, "_tmux", self.tmux):
            notify.send_tmux({"type": mode, "target": self.target, "template": "{message}"}, row)
        return row

    def test_literal_message_keeps_final_semicolon(self):
        row = self.send("tmux", "[my-api] [NEW] #12 AAA;")
        self.wait_for_line(lambda line: line == "notify-ready> " + notify.line(row))

    def test_prompt_executes_message_with_enter(self):
        self.send("prompt", r"printf '%s%s\n' prompt- executed")
        self.wait_for_line(lambda line: line == "prompt-executed")


class WorkflowTests(unittest.TestCase):
    def test_plugin_status_does_not_conflict_with_builtin_status(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            cfg = root / "notify.json"
            cfg.write_text(json.dumps({"outbox": str(root / "outbox.db"), "channels": []}))
            result = subprocess.run(
                [sys.executable, str(CHANGE_WATCH / "change_watch.py"), "--plugin", "gitlab_notify",
                 "notify-status", "--json"],
                env={**os.environ, "GITLAB_NOTIFY_CONFIG": str(cfg)},
                capture_output=True, text=True, check=True,
            )
        self.assertEqual(json.loads(result.stdout), [])

    def test_example_with_real_change_watch_and_fake_gitlab(self):
        try:
            import yaml
        except ImportError:
            self.skipTest("例の設定・DAG を読むのに PyYAML が要る")
        dag = AGENT / "change-watch-notify.yaml.example"
        graph = dagrun.load(dag)
        dagrun.check_shape(self, graph, dag.name)
        get, retry, send, commit = graph["steps"]
        self.assertEqual([step["name"] for step in graph["steps"]], ["get", "retry", "notify", "commit"])
        self.assertEqual([step.get("depends") for step in graph["steps"]], [None, ["get"], ["retry"], ["notify"]])
        args = get["with"]["args"]
        self.assertEqual([args[i + 1] for i, arg in enumerate(args) if arg == "--watch"],
                         ["title", "state", "labels", "assignee.username"])
        self.assertTrue(retry["continue_on"]["failure"])
        self.assertNotIn("failure", send.get("continue_on", {}))
        self.assertNotIn("--refresh", commit["with"]["args"])
        try:
            gl = FakeGitLab()
        except PermissionError as exc:
            self.skipTest(f"この環境では偽 GitLab のローカルソケットを開けない: {exc}")
        self.addCleanup(gl.close)
        gl.put(make_issue(1))
        root = Path(self.enterContext(tempfile.TemporaryDirectory()))
        conn = root / "connections.json"
        conn.write_text(json.dumps({"gitlab": [{"label": "default", "url": gl.url, "token": "t0ken"}]}))
        cfg = yaml.safe_load((AGENT / "change-watch.yaml.example").read_text(encoding="utf-8"))
        cfg["state_dir"] = str(root / "state")
        source = cfg["sources"]["gitlab-issues"]
        for key in ("schedule", "jitter_sec", "busy_wait_sec", "rate_limit"):
            source.pop(key)
        cfg_path = root / "config.json"
        cfg_path.write_text(json.dumps(cfg))
        notify_path = root / "notify.json"
        outbox = root / "outbox.db"
        log = root / "log.txt"
        notify_path.write_text(json.dumps({"repos": {"my-api": {}}, "outbox": str(outbox), "channels": [
            {"id": "log", "type": "log", "path": str(log)}
        ]}))
        aw = [sys.executable, str(CHANGE_WATCH / "change_watch.py")]
        env = {"GITLAB_PROJECT": "g/p", "GITLAB_NOTIFY_CONFIG": str(notify_path)}
        paths = {"/home/you/.github/connections.yaml": str(conn),
                 "/home/you/.config/change-watch/gitlab-notify-agent/change-watch.yaml": str(cfg_path)}

        def run():
            with redirect_stdout(io.StringIO()):
                return dagrun.run(dag, aw=aw, head=[], env=env, paths=paths)

        def get_change():
            argv = dagrun._argv(get["with"], aw, [], paths, {})
            result = subprocess.run(argv, env={**os.environ, **env}, check=True, capture_output=True, text=True)
            return json.loads(result.stdout) if result.stdout.strip() else None

        self.assertEqual(run(), {"get": 0, "retry": 0, "notify": None, "commit": None})
        gl.put(make_issue(2))
        pending = get_change()
        self.assertEqual((pending["key"], pending["kind"]), ("2", "appeared"))
        outbox.unlink()
        outbox.mkdir()
        self.assertEqual(run(), {"get": 0, "retry": 1, "notify": 1})
        self.assertEqual(get_change(), pending)
        self.assertFalse(log.exists())
        outbox.rmdir()
        log.mkdir()  # 配信だけが失敗した場合は commit する。
        self.assertEqual(run(), {"get": 0, "retry": 0, "notify": 0, "commit": 0})
        self.assertIsNone(get_change())
        with closing(sqlite3.connect(outbox)) as db:
            self.assertEqual(db.execute("SELECT rkey, status FROM outbox").fetchall(), [("2", "pending")])
            with db:
                db.execute("UPDATE outbox SET next_attempt_at = 0")
        gl.put(make_issue(3))
        self.assertEqual(run(), {"get": 0, "retry": 1, "notify": 0, "commit": 0})
        self.assertIsNone(get_change())
        with closing(sqlite3.connect(outbox)) as db:
            with db:
                db.execute("UPDATE outbox SET next_attempt_at = 0")
        log.rmdir()
        self.assertEqual(run(), {"get": 0, "retry": 0, "notify": None, "commit": None})
        self.assertEqual([line.split("  ", 1)[1] for line in log.read_text().splitlines()],
                         ["[my-api] [NEW] #2 issue 2", "[my-api] [NEW] #3 issue 3"])
