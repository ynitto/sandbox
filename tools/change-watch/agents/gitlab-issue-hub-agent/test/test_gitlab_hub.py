"""gitlab-issue-hub-agent のテスト（標準ライブラリと PyYAML。`python -m unittest discover -s test`）。

gitlab_hub.py の note・reply と、設定の例（change-watch.yaml.example）と Dagu のワークフローの例を本物の change-watch で
Dagu と同じ順（test/dagrun.py）に回したときの流れを確かめる。GitLab は手元の HTTP サーバで偽装する。
"""
from __future__ import annotations

import importlib.util
import io
import json
import os
import sys
import tempfile
import threading
import unittest
from contextlib import redirect_stderr, redirect_stdout
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

try:
    import yaml
except ImportError:      # pragma: no cover
    yaml = None

HERE = Path(__file__).resolve().parent
AGENT = HERE.parent
PROGRAM = AGENT / "gitlab_hub.py"
HANDLER = AGENT / "examples" / "on_request.py"
CHANGE_WATCH = HERE.parents[2] / "change_watch.py"   # tools/change-watch/agents/<エージェント>/test
spec = importlib.util.spec_from_file_location("gitlab_hub", PROGRAM)
gh = importlib.util.module_from_spec(spec)
spec.loader.exec_module(gh)

TOKEN = "glpat-test"
PROJECT = "123"
ISSUE = 42
BOT = {"id": 1, "username": "hub-bot"}
ALICE = {"id": 2, "username": "alice"}


def run_main(*argv) -> tuple[int, str, str]:
    out, err = io.StringIO(), io.StringIO()
    with redirect_stdout(out), redirect_stderr(err):
        code = gh.main(list(argv))
    return code, out.getvalue(), err.getvalue()


class FakeGitLab:
    """GET・POST /api/v4/projects/123/issues/42/notes（page・per_page）。POST はトークンの持ち主（BOT）が書く。"""

    def __init__(self):
        self.notes: list[dict] = []
        self.posted: list[str] = []
        self.lock = threading.Lock()
        outer = self
        path = f"/api/v4/projects/{PROJECT}/issues/{ISSUE}/notes"

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *a):
                pass

            def do_GET(self):
                if self.headers.get("PRIVATE-TOKEN") != TOKEN:
                    return self._send(401, {"message": "401 Unauthorized"})
                u = urlparse(self.path)
                if u.path != path:
                    return self._send(404, {"message": "404 Not found"})
                q = parse_qs(u.query)
                page, per = int(q.get("page", ["1"])[0]), int(q.get("per_page", ["20"])[0])
                with outer.lock:
                    items = sorted(outer.notes, key=lambda n: n["id"])
                self._send(200, items[(page - 1) * per: page * per])

            def do_POST(self):
                if self.headers.get("PRIVATE-TOKEN") != TOKEN:
                    return self._send(401, {"message": "401 Unauthorized"})
                if urlparse(self.path).path != path:
                    return self._send(404, {"message": "404 Not found"})
                body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))["body"]
                with outer.lock:
                    outer.posted.append(body)
                    note = outer._add(body, BOT)
                self._send(201, note)

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

    def _add(self, body: str, author: dict, system: bool = False) -> dict:
        note = {"id": 1000 + len(self.notes), "body": body, "author": dict(author), "system": system,
                "created_at": "2026-11-09T00:00:00Z", "updated_at": "2026-11-09T00:00:00Z", "noteable_iid": ISSUE}
        self.notes.append(note)
        return note

    def add(self, body: str, author: dict = ALICE, system: bool = False) -> dict:
        with self.lock:
            return self._add(body, author, system)

    def close(self):
        self.server.shutdown()
        self.server.server_close()


# ---------------------------------------------------------------------------
# gitlab_hub.py
# ---------------------------------------------------------------------------

class NoteTest(unittest.TestCase):
    def test_notify_has_the_mark_and_title_on_the_first_line(self):
        code, out, _ = run_main("note", "--title=夜間バッチ", "--text=終わりました\n2 件")
        self.assertEqual(code, 0)
        self.assertEqual(json.loads(out), {"body": "【通知】夜間バッチ\n\n終わりました\n2 件"})

    def test_quick_actions_are_disarmed(self):
        code, out, _ = run_main("note", "--title=t", "--text=/close\n  /assign @me\npath a/b")
        self.assertEqual(json.loads(out)["body"], "【通知】t\n\n\\/close\n  \\/assign @me\npath a/b")
        self.assertEqual(gh.disarm("/label ~x"), "\\/label ~x")

    def test_text_starting_with_dash_and_text_file(self):
        self.assertEqual(json.loads(run_main("note", "--text=-x")[1])["body"], "【通知】\n\n-x")
        f = Path(tempfile.mkdtemp()) / "b.txt"
        f.write_text("ファイルの本文\n", encoding="utf-8")
        self.assertEqual(json.loads(run_main("note", "--title=t", "--text-file", str(f))[1])["body"], "【通知】t\n\nファイルの本文")

    def test_nothing_to_write_is_a_usage_error(self):
        self.assertEqual(run_main("note")[0], gh.USAGE)
        self.assertEqual(run_main("note", "--text=a", "--text-file", "x")[0], gh.USAGE)


class ReplyTest(unittest.TestCase):
    def change(self, body: str) -> str:
        return json.dumps({"key": "1001", "kind": "appeared",
                           "record": {"id": 1001, "body": body, "author": ALICE, "system": False}}, ensure_ascii=False)

    def test_handler_gets_the_request_and_its_output_becomes_the_reply(self):
        script = Path(tempfile.mkdtemp()) / "h.py"
        script.write_text("import json, os, sys\nc = json.load(sys.stdin)\n"
                          "print(os.environ['HUB_SUBJECT'], os.environ['HUB_AUTHOR'], os.environ['HUB_NOTE_ID'], c['key'])\n"
                          "print(os.environ['HUB_TEXT'])\nprint('/close')\n", encoding="utf-8")
        code, out, _ = run_main("reply", self.change("【依頼】売上の集計\n\n先月分を\nお願いします"), "--",
                                sys.executable, str(script))
        self.assertEqual(code, 0)
        self.assertEqual(json.loads(out)["body"],
                         "【返信】売上の集計\n\n売上の集計 alice 1001 1001\n先月分を\nお願いします\n\\/close")

    def test_failure_replies_without_details_and_keeps_the_exit_code(self):
        code, out, err = run_main("reply", self.change("【依頼】x"), "--", sys.executable, "-c",
                                  "import sys; print('secret'); print('oops', file=sys.stderr); sys.exit(4)")
        self.assertEqual(code, 4)
        self.assertEqual(json.loads(out)["body"], "【返信】x\n\n処理できませんでした（終了コード 4）。")
        self.assertIn("oops", err)

    def test_missing_command_and_silent_handler(self):
        code, out, _ = run_main("reply", self.change("【依頼】x"), "--", str(Path(tempfile.mkdtemp()) / "nope"))
        self.assertEqual(code, gh.NOT_FOUND)
        self.assertIn("処理できませんでした", json.loads(out)["body"])
        self.assertEqual(run_main("reply", self.change("【依頼】x"), "--", sys.executable, "-c", "pass")[:2], (0, ""))
        self.assertEqual(run_main("reply", "not json", "--", "x")[0], gh.USAGE)


# ---------------------------------------------------------------------------
# 例の設定とワークフローを本物の change-watch で回す
# ---------------------------------------------------------------------------

class ExampleFlowTest(unittest.TestCase):
    def setUp(self):
        if yaml is None:
            self.skipTest("例の設定・DAG を読むのに PyYAML が要る")
        sys.path.insert(0, str(CHANGE_WATCH.parent / "test"))
        self.tmp = Path(tempfile.mkdtemp())
        self.gl = FakeGitLab()
        self.addCleanup(self.gl.close)
        cfg = yaml.safe_load((AGENT / "change-watch.yaml.example").read_text(encoding="utf-8"))
        self.assertNotIn("rules", cfg)                                   # 条件は change-watch に書かない
        cfg["state_dir"] = str(self.tmp / "state")
        src = cfg["sources"]["gitlab-hub-notes"]
        for k in ("schedule", "jitter_sec"):
            src.pop(k)
        src.update(retry={"attempts": 1}, failure_backoff_sec=0)
        self.cfg = self.tmp / "change-watch.yaml"
        self.cfg.write_text(yaml.safe_dump(cfg, allow_unicode=True), encoding="utf-8")
        self.conn = self.tmp / "connections.yaml"
        self.conn.write_text("gitlab:\n  - label: default\n    url: ${GITLAB_URL}\n    token: ${GITLAB_TOKEN}\n", encoding="utf-8")
        self.env = {**os.environ, "GITLAB_URL": self.gl.url, "GITLAB_TOKEN": TOKEN, "GITLAB_PROJECT": PROJECT,
                    "GITLAB_HUB_ISSUE": str(ISSUE)}
        self.paths = {"/home/you/.github/connections.yaml": str(self.conn),
                      "/home/you/.config/change-watch/gitlab-issue-hub-agent/change-watch.yaml": str(self.cfg),
                      "/home/you/.local/share/change-watch/agents/gitlab-issue-hub-agent/gitlab_hub.py": str(PROGRAM),
                      "/home/you/work/hub/on_request.py": str(HANDLER)}

    def run_dag(self, name: str, **params) -> dict:
        import dagrun
        dag = self.tmp / name                                            # python3 はこの Python に（Windows に python3 が無いことがある）
        dag.write_text((AGENT / name).read_text(encoding="utf-8").replace('"python3"', json.dumps(Path(sys.executable).as_posix())),
                       encoding="utf-8")
        dagrun.check_shape(self, dagrun.load(dag), dag.name)
        env = {**self.env, **{f"params.{k}": v for k, v in params.items()}}
        return dagrun.run(dag, aw=[sys.executable, str(CHANGE_WATCH)], head=[], env=env, paths=self.paths)

    def test_replies_to_requests_only_once(self):
        self.gl.add("前からあるコメント")
        self.gl.add("【依頼】前からある依頼")
        hub = "change-watch-issue-hub.yaml.example"
        self.assertEqual(self.run_dag(hub)["get"], 0)                    # 初回は今あるコメントを覚えるだけ
        self.assertEqual(self.gl.posted, [])

        self.gl.add("【依頼】売上の集計\n先月分をお願いします")          # メールから Power Automate が書いたもの
        self.gl.add("ふつうのコメント")
        self.gl.add("added ~1 label", system=True)
        self.gl.add("【依頼】在庫の確認", author=BOT)                    # Power Automate がエージェントと同じトークンで書いても依頼になる
        runs = [self.run_dag(hub) for _ in range(8)]
        self.assertIn({"get": 0, "request": 0, "reply": 0, "commit": 0}, runs)
        self.assertIn({"get": 0, "request": None, "reply": None, "commit": 0}, runs)
        self.assertEqual(runs[-1]["commit"], None)                       # 見終えたら、もう何もしない（自分の返信には返信しない）
        self.assertEqual(self.gl.posted, ["【返信】売上の集計\n\n「売上の集計」を受け付けました（1 行）。",
                                          "【返信】在庫の確認\n\n「在庫の確認」を受け付けました（0 行）。"])

    def test_failed_request_is_answered_and_not_retried(self):
        self.run_dag("change-watch-issue-hub.yaml.example")
        bad = self.tmp / "bad.py"
        bad.write_text("import sys; sys.exit(5)\n", encoding="utf-8")
        self.paths["/home/you/work/hub/on_request.py"] = str(bad)
        self.gl.add("【依頼】壊れる依頼")
        r = self.run_dag("change-watch-issue-hub.yaml.example")
        self.assertEqual(r, {"get": 0, "request": 5, "reply": 0, "commit": 0})
        self.assertEqual(self.gl.posted, ["【返信】壊れる依頼\n\n処理できませんでした（終了コード 5）。"])
        self.assertEqual(self.run_dag("change-watch-issue-hub.yaml.example"),                  # 次は自分の返信を見て、何もしない
                         {"get": 0, "request": None, "reply": None, "commit": 0})
        self.assertEqual(self.run_dag("change-watch-issue-hub.yaml.example")["commit"], None)   # 失敗した依頼はやり直さない
        self.assertEqual(len(self.gl.posted), 1)

    def test_notify_posts_a_marked_comment(self):
        r = self.run_dag("change-watch-issue-hub-notify.yaml.example", TITLE="夜間バッチ", BODY="終わりました\n/close")
        self.assertEqual(r, {"compose": 0, "post": 0})
        self.assertEqual(self.gl.posted, ["【通知】夜間バッチ\n\n終わりました\n\\/close"])

    def test_maintenance_runs(self):
        self.assertEqual(self.run_dag("change-watch-gitlab-issue-hub-agent-maintenance.yaml.example"), {"gc": 0})


if __name__ == "__main__":
    unittest.main()
