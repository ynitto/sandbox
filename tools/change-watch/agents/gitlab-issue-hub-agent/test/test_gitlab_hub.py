"""gitlab-issue-hub-agent のテスト（標準ライブラリと PyYAML。`python -m unittest discover -s test`）。

gitlab_hub.py のフィルタ gitlab_issue_api・note・fields と、設定の例（change-watch.yaml.example）と Dagu のワークフローの例を本物の change-watch で
Dagu と同じ順（test/dagrun.py）に回したときの流れを確かめる。GitLab は手元の HTTP サーバで偽装する。
"""
from __future__ import annotations

import importlib.util
import io
import json
import os
import subprocess
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
PROJECT = "group/hub"
ISSUE = 42
BOT = {"id": 1, "username": "hub-bot"}
ALICE = {"id": 2, "username": "alice"}


def run_main(*argv) -> tuple[int, str, str]:
    out, err = io.StringIO(), io.StringIO()
    with redirect_stdout(out), redirect_stderr(err):
        code = gh.main(list(argv))
    return code, out.getvalue(), err.getvalue()


class FakeGitLab:
    """GET・POST /api/v4/projects/group%2Fhub/issues/42/notes（page・per_page）と GET・PUT …/issues/42。書くのはトークンの持ち主（BOT）。"""

    def __init__(self):
        self.notes: list[dict] = []
        self.posted: list[str] = []
        self.updates: list[dict] = []
        self.issue = {"iid": ISSUE, "title": "メール連携のハブ", "description": "", "labels": [], "state": "opened"}
        self.lock = threading.Lock()
        outer = self
        issue_path = f"/api/v4/projects/{PROJECT.replace('/', '%2F')}/issues/{ISSUE}"
        path = issue_path + "/notes"

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *a):
                pass

            def do_GET(self):
                if self.headers.get("PRIVATE-TOKEN") != TOKEN:
                    return self._send(401, {"message": "401 Unauthorized"})
                u = urlparse(self.path)
                if u.path == issue_path:
                    with outer.lock:
                        return self._send(200, dict(outer.issue))
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

            def do_PUT(self):
                if self.headers.get("PRIVATE-TOKEN") != TOKEN:
                    return self._send(401, {"message": "401 Unauthorized"})
                if urlparse(self.path).path != issue_path:
                    return self._send(404, {"message": "404 Not found"})
                body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
                with outer.lock:
                    outer.updates.append(body)
                    if "add_labels" in body:
                        outer.issue["labels"] = sorted(set(outer.issue["labels"]) | set(body["add_labels"].split(",")))
                    outer.issue.update({k: v for k, v in body.items() if k in ("title", "description")})
                    issue = dict(outer.issue)
                self._send(200, issue)

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

class IssueApiTest(unittest.TestCase):
    def test_issue_url_becomes_the_api_url(self):
        f = gh.gitlab_issue_api
        self.assertEqual(f("https://gl.example.com/group/sub/proj/-/issues/42"),
                         "https://gl.example.com/api/v4/projects/group%2Fsub%2Fproj/issues/42")
        self.assertEqual(f("https://gl.example.com/g/p/-/issues/7#note_3"), "https://gl.example.com/api/v4/projects/g%2Fp/issues/7")
        self.assertEqual(f("https://gl.example.com/g/p/issues/7/"), "https://gl.example.com/api/v4/projects/g%2Fp/issues/7")
        self.assertEqual(f("https://gl.example.com/g/p/-/work_items/5?x=1"), "https://gl.example.com/api/v4/projects/g%2Fp/issues/5")
        self.assertEqual(f("https://gl.example.com/gitlab/api/v4/projects/9/issues/1/"),   # API の URL はそのまま
                         "https://gl.example.com/gitlab/api/v4/projects/9/issues/1")

    def test_other_urls_are_refused(self):
        for bad in ("", "https://gl.example.com/g/p/-/merge_requests/1", "gl.example.com/g/p/-/issues/1"):
            with self.subTest(bad), self.assertRaises(ValueError):
                gh.gitlab_issue_api(bad)


class NoteTest(unittest.TestCase):
    def test_note_is_the_text_as_is(self):
        code, out, _ = run_main("note", "--text=夜間バッチが終わりました\n2 件\n")
        self.assertEqual(code, 0)
        self.assertEqual(json.loads(out), {"body": "夜間バッチが終わりました\n2 件"})

    def test_quick_actions_are_disarmed(self):
        code, out, _ = run_main("note", "--text=/close\n  /assign @me\npath a/b")
        self.assertEqual(json.loads(out)["body"], "\\/close\n  \\/assign @me\npath a/b")
        self.assertEqual(gh.disarm("/label ~x"), "\\/label ~x")

    def test_text_starting_with_dash_and_text_file(self):
        self.assertEqual(json.loads(run_main("note", "--text=-x")[1])["body"], "-x")
        f = Path(tempfile.mkdtemp()) / "b.txt"
        f.write_text("ファイルの本文\n", encoding="utf-8")
        self.assertEqual(json.loads(run_main("note", "--text-file", str(f))[1])["body"], "ファイルの本文")

    def test_command_output_becomes_the_text(self):
        code, out, _ = run_main("note", "--", sys.executable, "-c", "import sys; print('結果: ' + sys.argv[1]); print('/close')", "{}")
        self.assertEqual((code, json.loads(out)["body"]), (0, "結果: {}\n\\/close"))

    def test_failed_command_writes_nothing_and_keeps_the_exit_code(self):
        code, out, err = run_main("note", "--", sys.executable, "-c",
                                  "import sys; print('途中'); print('oops', file=sys.stderr); sys.exit(4)")
        self.assertEqual((code, out), (4, ""))
        self.assertIn("oops", err)
        code, out, _ = run_main("note", "--", str(Path(tempfile.mkdtemp()) / "nope"))
        self.assertEqual((code, out), (gh.NOT_FOUND, ""))

    def test_empty_text_writes_nothing(self):
        self.assertEqual(run_main("note", "--text= ")[:2], (0, ""))
        self.assertEqual(run_main("note", "--", sys.executable, "-c", "pass")[:2], (0, ""))

    def test_text_must_come_from_exactly_one_place(self):
        self.assertEqual(run_main("note")[0], gh.USAGE)
        self.assertEqual(run_main("note", "--text=a", "--text-file", "x")[0], gh.USAGE)
        self.assertEqual(run_main("note", "--text=a", "--", "echo")[0], gh.USAGE)


# ---------------------------------------------------------------------------
# 例の設定とワークフローを本物の change-watch で回す
# ---------------------------------------------------------------------------

class FieldsTest(unittest.TestCase):
    def test_pairs_become_a_json_object(self):
        code, out, _ = run_main("fields", 'title=【完了】"引用"の題', "add_labels=完了,済", "description=/close\n本文", "a=b=c")
        self.assertEqual(code, 0)
        self.assertEqual(json.loads(out), {"title": '【完了】"引用"の題', "add_labels": "完了,済",
                                           "description": "\\/close\n本文", "a": "b=c"})

    def test_bad_pairs_are_refused(self):
        self.assertEqual(run_main("fields", "title")[0], gh.USAGE)
        self.assertEqual(run_main("fields", "=x")[0], gh.USAGE)


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
        for src in cfg["sources"].values():
            for k in ("schedule", "jitter_sec"):
                src.pop(k)
            src.update(retry={"attempts": 1}, failure_backoff_sec=0)
        self.cfg = self.tmp / "change-watch.yaml"
        self.cfg.write_text(yaml.safe_dump(cfg, allow_unicode=True), encoding="utf-8")
        self.conn = self.tmp / "connections.yaml"
        self.conn.write_text("gitlab:\n  - label: default\n    url: ${GITLAB_URL}\n    token: ${GITLAB_TOKEN}\n", encoding="utf-8")
        self.env = {**os.environ, "GITLAB_URL": "http://127.0.0.1:9", "GITLAB_TOKEN": TOKEN,   # 接続の url は使わない
                    "GITLAB_HUB_ISSUE_URL": f"{self.gl.url}/{PROJECT}/-/issues/{ISSUE}"}
        self.paths = {"/home/you/.github/connections.yaml": str(self.conn),
                      "/home/you/.config/change-watch/gitlab-issue-hub-agent/change-watch.yaml": str(self.cfg),
                      "/home/you/.local/share/change-watch/agents/gitlab-issue-hub-agent/gitlab_hub.py": str(PROGRAM),
                      "/home/you/work/hub/on_request.py": str(HANDLER)}

    def run_dag(self, name: str, **params) -> dict:
        import dagrun
        dag = self.tmp / Path(name).name                                            # python3 はこの Python に（Windows に python3 が無いことがある）
        dag.write_text((AGENT / name).read_text(encoding="utf-8").replace('"python3"', json.dumps(Path(sys.executable).as_posix())),
                       encoding="utf-8")
        dagrun.check_shape(self, dagrun.load(dag), dag.name)
        env = {**self.env, **{f"params.{k}": v for k, v in params.items()}}
        return dagrun.run(dag, aw=[sys.executable, str(CHANGE_WATCH)], head=[], env=env, paths=self.paths)

    def test_replies_to_matching_comments_only_once(self):
        self.gl.add("前からあるコメント")
        self.gl.add("【依頼】前からある依頼")
        hub = "change-watch-issue-hub.yaml.example"
        self.assertEqual(self.run_dag(hub)["get"], 0)                    # 初回は今あるコメントを覚えるだけ
        self.assertEqual(self.gl.posted, [])

        self.gl.add("【依頼】売上の集計\n先月分をお願いします")          # メールから Power Automate が書いたもの
        self.gl.add("ふつうのコメント")
        self.gl.add("added ~1 label", system=True)
        self.gl.add("【依頼】在庫の確認", author=BOT)                    # エージェントと同じトークンで書いても、条件は 1 行目だけ
        runs = [self.run_dag(hub) for _ in range(8)]
        self.assertIn({"get": 0, "request": 0, "reply": 0, "commit": 0}, runs)
        self.assertIn({"get": 0, "request": None, "reply": None, "commit": 0}, runs)
        self.assertEqual(runs[-1]["commit"], None)                       # 見終えたら、もう何もしない（自分のコメントには当たらない）
        self.assertEqual(self.gl.posted, ["【返信】売上の集計\n\nalice さんの依頼を受け付けました（1 行）。",
                                          "【返信】在庫の確認\n\nhub-bot さんの依頼を受け付けました（0 行）。"])

    def test_failed_command_comments_nothing_and_is_not_retried(self):
        hub = "change-watch-issue-hub.yaml.example"
        self.run_dag(hub)
        bad = self.tmp / "bad.py"
        bad.write_text("import sys; print('途中'); sys.exit(5)\n", encoding="utf-8")
        self.paths["/home/you/work/hub/on_request.py"] = str(bad)
        self.gl.add("【依頼】壊れる依頼")
        self.assertEqual(self.run_dag(hub), {"get": 0, "request": 5, "reply": None, "commit": 0})
        self.assertEqual(self.gl.posted, [])
        self.assertEqual(self.run_dag(hub)["commit"], None)               # やり直さない

    def test_update_adds_a_comment(self):
        dag = "change-watch-issue-hub-update.yaml.example"
        self.assertEqual(self.run_dag(dag, COMMENT="夜間バッチが終わりました\n/close"), {"compose": 0, "comment": 0})
        self.assertEqual(self.gl.posted, ["夜間バッチが終わりました\n\\/close"])
        self.assertEqual(self.run_dag(dag, COMMENT=""), {"compose": 0, "comment": None})   # 空なら書かない
        self.assertEqual(len(self.gl.posted), 1)
        self.assertEqual(self.run_dag("change-watch-issue-hub.yaml.example")["get"], 0)
        self.gl.add("あとから付いたコメント")
        self.assertEqual(self.run_dag("change-watch-issue-hub.yaml.example")["request"], None)

    def test_issue_itself_changes_are_detected_and_it_can_be_edited(self):
        def cw(*args):
            p = subprocess.run([sys.executable, str(CHANGE_WATCH), "--plugin", str(PROGRAM), "--config", str(self.cfg),
                                "--connections", str(self.conn), *args], capture_output=True, text=True, encoding="utf-8",
                               env=self.env)
            self.assertEqual(p.returncode, 0, p.stderr)
            return p.stdout.strip()
        get = ("get", "--source", "gitlab-hub-issue", "--consumer", "pa", "--watch", "labels", "--watch", "description", "--one")
        self.assertEqual(cw(*get), "")                                   # 初回は覚えるだけ
        cw("call", "PUT", "{{ env.GITLAB_HUB_ISSUE_URL | gitlab_issue_api }}", "--source", "gitlab-hub-notes",
           "--json", '{"add_labels": "完了"}')
        self.assertEqual(self.gl.updates, [{"add_labels": "完了"}])
        change = json.loads(cw(*get))
        self.assertEqual((change["kind"], change["record"]["labels"], change["before"]["labels"]), ("changed", ["完了"], []))
        cw("commit", "--source", "gitlab-hub-issue", "--consumer", "pa", change["key"])
        self.assertEqual(cw(*get), "")

    def test_get_post_example(self):
        dag = "examples/change-watch-issue-hub-get-post.yaml.example"
        self.assertEqual(self.run_dag(dag)["get"], 0)                    # 初回は今の issue を覚えるだけ
        with self.gl.lock:
            self.gl.issue["description"] = "説明を足した"
        self.assertEqual(self.run_dag(dag), {"get": 0, "compose": None, "comment": None, "fields": None,
                                             "retitle": None, "commit": 0})   # 条件に合わない変化は見るだけ
        with self.gl.lock:
            self.gl.issue["labels"] = ["完了"]
            self.gl.issue["title"] = 'メール連携の"ハブ"'
        self.assertEqual(self.run_dag(dag), {"get": 0, "compose": 0, "comment": 0, "fields": 0, "retitle": 0, "commit": 0})
        self.assertEqual(self.gl.posted, ['「メール連携の"ハブ"」が完了になりました。'])
        self.assertEqual(self.gl.updates, [{"title": '【完了】メール連携の"ハブ"'}])
        self.assertEqual(self.gl.issue["title"], '【完了】メール連携の"ハブ"')
        self.assertEqual(self.run_dag(dag)["commit"], None)              # 自分が変えた題では次に起きない（commit --refresh）
        with self.gl.lock:
            self.gl.issue["description"] = "もう一度変えた"
        self.assertEqual(self.run_dag(dag)["comment"], None)             # 題が【完了】で始まるので、二度は書かない
        self.assertEqual(len(self.gl.posted), 1)

    def test_maintenance_runs(self):
        self.assertEqual(self.run_dag("change-watch-gitlab-issue-hub-agent-maintenance.yaml.example"), {"gc": 0})


if __name__ == "__main__":
    unittest.main()
