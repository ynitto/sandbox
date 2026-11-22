"""gitlab-issue-hub-agent のテスト（標準ライブラリと PyYAML。`python -m unittest discover -s test`）。

gitlab_hub.py のフィルタ gitlab_issue_api とコマンド note・description・api-url・fields（直接と、change-watch を通して）と、設定の例（change-watch.yaml.example）と Dagu のワークフローの例を本物の change-watch で
Dagu と同じ順（test/dagrun.py）に回したときの流れを確かめる。GitLab は手元の HTTP サーバで偽装する。
"""
from __future__ import annotations

import datetime
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
from unittest.mock import patch

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
OTHER = 7                # ハブでない issue（説明を書き換える例が、URL で指す）
BOT = {"id": 1, "username": "hub-bot"}
ALICE = {"id": 2, "username": "alice"}


def run_main(*argv) -> tuple[int, str, str]:
    out, err = io.StringIO(), io.StringIO()
    with redirect_stdout(out), redirect_stderr(err):
        code = gh.main(list(argv))
    return code, out.getvalue(), err.getvalue()


class BundleTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.state = Path(temporary.name) / "nested" / "bundle.json"
        self.start = datetime.datetime(2026, 11, 22, 12, tzinfo=datetime.timezone.utc)

    def bundle(self, command, *args, minutes=0):
        handler = "cmd_" + command.replace("-", "_")
        now = self.start + datetime.timedelta(minutes=minutes)
        original = getattr(gh, handler)
        with patch.object(gh, handler, lambda a: original(a, now=now)):
            return run_main(command, "--state-file", str(self.state), *args)

    def add(self, text, repo="group/project", kind="ci", **kwargs):
        return self.bundle("bundle-add", f"--repo={repo}", f"--kind={kind}", f"--text={text}", **kwargs)

    def pending(self):
        state = json.loads(self.state.read_text(encoding="utf-8"))
        self.assertEqual(state["version"], 1)
        return state["bundles"]

    def test_groups_and_flushes_expired_bundles_in_one_comment(self):
        self.assertEqual(self.add("最初の通知\n/close"), (0, "", ""))
        self.assertEqual(self.add("次の通知", minutes=9), (0, "", ""))
        self.assertEqual(self.add("別の repo", repo="other"), (0, "", ""))
        self.assertEqual(self.add("別の種別", kind="release", minutes=9), (0, "", ""))
        pending = self.pending()
        self.assertEqual(set(pending), {"group/project\nci", "other\nci", "group/project\nrelease"})
        self.assertEqual(len(pending["group/project\nci"]["items"]), 2)
        self.assertEqual(self.bundle("bundle-flush", minutes=9), (0, "", ""))
        code, out, err = self.bundle("bundle-flush", minutes=10)
        self.assertEqual((code, err), (0, ""))
        body = json.loads(out)["body"]
        for expected in ("group/project / ci（2件）", "other / ci（1件）", "最初の通知",
                         "\\/close", "次の通知", "別の repo", self.start.isoformat(),
                         (self.start + datetime.timedelta(minutes=9)).isoformat()):
            self.assertIn(expected, body)
        self.assertNotIn("別の種別", body)
        self.assertEqual(set(self.pending()), {"group/project\nrelease"})

    def test_add_rolls_over_expired_bundle(self):
        self.assertEqual(self.add("old"), (0, "", ""))
        code, out, err = self.add("new", minutes=10)
        self.assertEqual((code, err), (0, ""))
        self.assertIn("old", json.loads(out)["body"])
        self.assertNotIn("new", out)
        bundle = self.pending()["group/project\nci"]
        at = (self.start + datetime.timedelta(minutes=10)).isoformat()
        self.assertEqual(bundle, {"first_at": at, "items": [{"at": at, "text": "new"}]})

    def test_empty_flush_all_and_output_file(self):
        self.assertEqual(self.add(" \n"), (0, "", ""))
        self.assertFalse(self.state.exists())
        self.assertEqual(self.add("pending"), (0, "", ""))
        out = self.state.with_name("note.json")
        self.assertEqual(self.bundle("bundle-flush", "--flush-all", "--out", str(out)), (0, f"{out}\n", ""))
        self.assertIn("pending", json.loads(out.read_text(encoding="utf-8"))["body"])
        self.assertEqual(self.pending(), {})
        self.assertEqual(self.bundle("bundle-flush", "--flush-all", "--out", str(out)), (0, "", ""))

    def test_output_cannot_overwrite_state(self):
        self.assertEqual(self.add("kept"), (0, "", ""))
        before = self.state.read_bytes()
        code, out, err = self.bundle("bundle-flush", "--flush-all", "--out", str(self.state))
        self.assertEqual((code, out), (2, ""))
        self.assertIn("--out と --state-file", err)
        self.assertEqual(self.state.read_bytes(), before)

    def test_rollover_removes_output_if_state_save_fails(self):
        self.assertEqual(self.add("old"), (0, "", ""))
        before = self.state.read_bytes()
        out = self.state.with_name("note.json")

        def fail_save(path, bundles):
            self.assertIn("old", json.loads(out.read_text(encoding="utf-8"))["body"])
            raise OSError("state save failed")

        with patch.object(gh, "_save_bundles", side_effect=fail_save):
            code, stdout, err = self.bundle(
                "bundle-add", "--repo=group/project", "--kind=ci", "--text=new",
                "--out", str(out), minutes=10,
            )
        self.assertEqual((code, stdout), (2, ""))
        self.assertIn("state save failed", err)
        self.assertFalse(out.exists())
        self.assertEqual(self.state.read_bytes(), before)

    def test_corrupt_bundle_is_discarded_individually(self):
        self.assertEqual(self.add("kept"), (0, "", ""))
        valid = self.pending()
        state = {"version": 1, "bundles": {**valid, "broken": {}}}
        self.state.write_text(json.dumps(state), encoding="utf-8")
        code, out, err = self.add("new", repo="another")
        self.assertEqual((code, out), (0, ""))
        self.assertIn("警告", err)
        self.assertIn("'broken'", err)
        pending = self.pending()
        self.assertEqual(set(pending), {"group/project\nci", "another\nci"})
        self.assertEqual(pending["group/project\nci"], valid["group/project\nci"])

    def test_corrupt_state_is_replaced(self):
        self.state.parent.mkdir()
        for broken in ("{", '{"version": 1, "bundles": {"r\\nk": {"first_at": "bad", "items": []}}}'):
            with self.subTest(broken=broken):
                self.state.write_text(broken, encoding="utf-8")
                code, out, err = self.add("kept")
                self.assertEqual((code, out), (0, ""))
                self.assertIn("警告", err)
                self.assertEqual(self.pending()["group/project\nci"]["items"][0]["text"], "kept")

    def test_broken_text_and_failed_output_preserve_pending_state(self):
        self.assertEqual(self.add("kept"), (0, "", ""))
        before = self.state.read_bytes()
        code, out, _ = self.add("broken \ufffd", minutes=10)
        self.assertEqual((code, out), (2, ""))
        code, out, _ = self.bundle("bundle-flush", "--flush-all", "--out", str(self.state.parent))
        self.assertEqual((code, out), (2, ""))
        self.assertEqual(self.state.read_bytes(), before)

    def test_invalid_arguments_exit_with_status_2(self):
        for flag, values in (("repo", ("", " \t", "r\np")), ("kind", ("", " \t", "ci\rtest")),
                             ("window-minutes", ("0", "-1", "1.5"))):
            for value in values:
                args = {"repo": "r", "kind": "ci", flag: value}
                with self.subTest(flag=flag, value=value), self.assertRaises(SystemExit) as raised:
                    self.bundle("bundle-add", *(f"--{key}={val}" for key, val in args.items()), "--text=notice")
                self.assertEqual(raised.exception.code, 2)
        with self.assertRaises(SystemExit) as raised:
            self.bundle("bundle-flush", "--window-minutes=0")
        self.assertEqual(raised.exception.code, 2)
        self.assertFalse(self.state.exists())

    def test_plugin_registration_and_custom_window(self):
        command = [sys.executable, str(CHANGE_WATCH), "--plugin", str(PROGRAM)]
        added = subprocess.run(
            [*command, "bundle-add", "--state-file", str(self.state), "--repo=r", "--kind=ci",
             "--", sys.executable, "-c", "print('from command')"],
            capture_output=True, text=True, check=True,
        )
        self.assertEqual(added.stdout, "")
        received = datetime.datetime.fromisoformat(self.pending()["r\nci"]["first_at"])
        self.start = received + datetime.timedelta(minutes=1)
        code, out, err = self.bundle("bundle-flush", "--window-minutes=1")
        self.assertEqual((code, err), (0, ""))
        self.assertIn("from command", json.loads(out)["body"])
        flushed = subprocess.run([*command, "bundle-flush", "--state-file", str(self.state), "--flush-all"],
                                 capture_output=True, text=True, check=True)
        self.assertEqual(flushed.stdout, "")


class FakeGitLab:
    """GET・POST /api/v4/projects/group%2Fhub/issues/42/notes（page・per_page）と GET・PUT …/issues/42。書くのはトークンの持ち主（BOT）。"""

    def __init__(self):
        self.notes: list[dict] = []
        self.posted: list[str] = []
        self.deleted: list[int] = []
        self.next_id = 999
        self.other_posted: list[str] = []
        self.updates: list[dict] = []
        self.created: dict[int, dict] = {}                   # プロジェクトに新しく立てた issue（iid ごと）
        self.removed: list[int] = []                         # 消した issue
        self.now = lambda: datetime.datetime.now(datetime.timezone.utc).isoformat()
        self.issue = {"iid": ISSUE, "title": "メール連携のハブ", "description": "", "labels": [], "state": "opened"}
        self.other = {"iid": OTHER, "title": "ほかの issue", "description": "前の説明", "labels": [], "state": "opened"}
        self.lock = threading.Lock()
        outer = self
        issue_path = f"/api/v4/projects/{PROJECT.replace('/', '%2F')}/issues/{ISSUE}"
        path = issue_path + "/notes"
        other_path = f"/api/v4/projects/{PROJECT.replace('/', '%2F')}/issues/{OTHER}"
        project_path = f"/api/v4/projects/{PROJECT.replace('/', '%2F')}"

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
                if u.path == project_path + "/issues":                   # 知らせ用プロジェクトの issue の一覧
                    q = parse_qs(u.query)
                    page, per = int(q.get("page", ["1"])[0]), int(q.get("per_page", ["20"])[0])
                    with outer.lock:
                        items = [dict(i) for i in sorted(outer.created.values(), key=lambda i: i["created_at"])
                                 if q.get("state", [""])[0] in ("", i["state"])]
                    return self._send(200, items[(page - 1) * per: page * per])
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
                sent = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
                if urlparse(self.path).path == project_path + "/issues":
                    with outer.lock:
                        iid = 100 + len(outer.created)
                        outer.created[iid] = {"iid": iid, "state": "opened", "created_at": outer.now(),
                                              "labels": sent.get("labels", "").split(",")
                                              if sent.get("labels") else [], **{k: sent[k] for k in ("title", "description")}}
                        return self._send(201, dict(outer.created[iid]))
                body = sent["body"]
                if urlparse(self.path).path == other_path + "/notes":
                    with outer.lock:
                        outer.other_posted.append(body)
                    return self._send(201, {"id": 1, "body": body})
                if urlparse(self.path).path != path:
                    return self._send(404, {"message": "404 Not found"})
                with outer.lock:
                    outer.posted.append(body)
                    note = outer._add(body, BOT)
                self._send(201, note)

            def do_DELETE(self):
                if self.headers.get("PRIVATE-TOKEN") != TOKEN:
                    return self._send(401, {"message": "401 Unauthorized"})
                head, _, iid = urlparse(self.path).path.rpartition("/")
                if head == project_path + "/issues" and iid.isdigit() and int(iid) in outer.created:
                    with outer.lock:
                        outer.created.pop(int(iid))
                        outer.removed.append(int(iid))
                    self.send_response(204)
                    self.end_headers()
                    return
                head, _, note_id = urlparse(self.path).path.rpartition("/")
                with outer.lock:
                    found = [n for n in outer.notes if head == path and str(n["id"]) == note_id]
                    if not found:
                        return self._send(404, {"message": "404 Not found"})
                    outer.notes.remove(found[0])
                    outer.deleted.append(found[0]["id"])
                self.send_response(204)
                self.end_headers()

            def do_PUT(self):
                if self.headers.get("PRIVATE-TOKEN") != TOKEN:
                    return self._send(401, {"message": "401 Unauthorized"})
                body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
                head, _, iid = urlparse(self.path).path.rpartition("/")
                if head == project_path + "/issues" and iid.isdigit() and int(iid) in outer.created:
                    with outer.lock:
                        if body.get("state_event") == "close":
                            outer.created[int(iid)]["state"] = "closed"
                        return self._send(200, dict(outer.created[int(iid)]))
                if urlparse(self.path).path == other_path:
                    with outer.lock:
                        outer.other.update(body)
                        return self._send(200, dict(outer.other))
                if urlparse(self.path).path != issue_path:
                    return self._send(404, {"message": "404 Not found"})
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

    def _add(self, body: str, author: dict, system: bool = False, created_at: str = "2026-11-09T00:00:00Z") -> dict:
        self.next_id += 1
        note = {"id": self.next_id, "body": body, "author": dict(author), "system": system,
                "created_at": created_at, "updated_at": created_at, "noteable_iid": ISSUE}
        self.notes.append(note)
        return note

    def add(self, body: str, author: dict = ALICE, system: bool = False, **kw) -> dict:
        with self.lock:
            return self._add(body, author, system, **kw)

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

    def test_invalid_utf8_file_is_reported_without_output(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "body.txt"
            path.write_bytes(b"\x81 ")                     # UTF-8 でも Shift_JIS でもない
            code, out, err = run_main("note", "--text-file", str(path))
        self.assertEqual((code, out), (gh.USAGE, ""))
        self.assertIn("本文のファイルを文字として読めません", err)

    def test_shift_jis_and_utf16_files_are_read_without_mojibake(self):
        text = "夜間バッチが終わりました\n- 件数: 12 件"
        with tempfile.TemporaryDirectory() as tmp:
            for name, raw in (("utf8.txt", text.encode("utf-8")), ("bom.txt", b"\xef\xbb\xbf" + text.encode("utf-8")),
                              ("sjis.txt", text.encode("cp932")),
                              ("utf16.txt", text.replace("\n", "\r\n").encode("utf-16")),   # PowerShell 5 の Out-File の既定
                              ("crlf.txt", text.replace("\n", "\r\n").encode("cp932"))):
                with self.subTest(name=name):
                    path = Path(tmp) / name
                    path.write_bytes(raw)
                    code, out, err = run_main("note", "--text-file", str(path))
                    self.assertEqual((code, json.loads(out)["body"]), (0, text), err)

    def test_shift_jis_command_output_is_read_without_mojibake(self):
        # Windows の多くのコマンド（PowerShell・bat など）は、パイプへ Shift_JIS で出す
        code, out, err = run_main("note", "--", sys.executable, "-c",
                                  "import sys; sys.stdout.buffer.write('結果: 正常\\n'.encode('cp932'))")
        self.assertEqual((code, json.loads(out)["body"]), (0, "結果: 正常"), err)
        code, out, err = run_main("note", "--", sys.executable, "-c",                  # Windows の改行も \n にそろえる
                                  "import sys; sys.stdout.buffer.write('一行目\\r\\n二行目\\r\\n'.encode('cp932'))")
        self.assertEqual((code, json.loads(out)["body"]), (0, "一行目\n二行目"), err)
        code, out, err = run_main("note", "--", sys.executable, "-c", "import sys; sys.stdout.buffer.write(b'\\x81 ')")
        self.assertEqual((code, out), (gh.USAGE, ""))
        self.assertIn("コマンドの出力を文字として読めません", err)

    def test_mojibake_is_refused_instead_of_posted(self):
        # 前のステップの出力を ${…} で渡すと、Dagu が UTF-8 でない文字を U+FFFD にしてから渡してくる
        for argv in (("note", "--text=夜間\nバッチ\ufffd\ufffd"), ("description", "--text=\ufffd"),
                     ("fields", "title=\ufffd完了")):
            with self.subTest(argv=argv):
                code, out, err = run_main(*argv)
                self.assertEqual((code, out), (gh.USAGE, ""))
                self.assertIn("文字化けした文字（U+FFFD）", err)
        self.assertIn("2 行目", run_main("note", "--text=夜間\nバッチ\ufffd")[2])

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

class IssueTest(unittest.TestCase):
    def test_issue_title_uses_first_line_of_direct_text(self):
        code, out, err = run_main("issue", "--text=一行目\r二行目\r\n三行目")
        self.assertEqual((code, err), (0, ""))
        self.assertEqual(json.loads(out), {"title": "一行目", "description": "一行目\n二行目\n三行目"})

    def test_large_retention_period_waits(self):
        record = {"state": "opened", "created_at": "2000-01-01T00:00:00Z",
                  "system": False, "author": {"username": "hub-bot"}}
        change = "--change=" + json.dumps({"kind": "appeared", "record": record})
        for command, options in (
            ("check-old-issue", ()),
            ("check-old-note", ("--author=hub-bot",)),
        ):
            with self.subTest(command=command):
                self.assertEqual(run_main(command, change, "--days=1000000000", *options), (0, "wait\n", ""))


class FieldsTest(unittest.TestCase):
    def test_pairs_become_a_json_object(self):
        code, out, _ = run_main("fields", 'title=【完了】"引用"の題', "add_labels=完了,済", "description=/close\n本文", "a=b=c")
        self.assertEqual(code, 0)
        self.assertEqual(json.loads(out), {"title": '【完了】"引用"の題', "add_labels": "完了,済",
                                           "description": "\\/close\n本文", "a": "b=c"})

    def test_description_takes_the_text_like_note(self):
        code, out, _ = run_main("description", "--text=1 行目\n/close\n")
        self.assertEqual((code, json.loads(out)), (gh.OK, {"description": "1 行目\n\\/close"}))
        code, out, _ = run_main("description", "--", sys.executable, "-c", "print('出力')")
        self.assertEqual((code, json.loads(out)), (gh.OK, {"description": "出力"}))
        self.assertEqual(run_main("description", "--text= ")[:2], (gh.OK, ""))       # 空なら何も出さない（説明を消さない）

    def test_api_url_of_the_given_issue_or_the_hub(self):
        old = os.environ.get("GITLAB_HUB_ISSUE_URL")
        os.environ["GITLAB_HUB_ISSUE_URL"] = "https://gitlab.example.com/group/hub/-/issues/42"
        self.addCleanup(lambda: os.environ.pop("GITLAB_HUB_ISSUE_URL") if old is None else os.environ.update(GITLAB_HUB_ISSUE_URL=old))
        self.assertEqual(run_main("api-url", "--url=https://gitlab.example.com/a/b/-/issues/7")[:2],
                         (gh.OK, "https://gitlab.example.com/api/v4/projects/a%2Fb/issues/7\n"))
        for argv in (["api-url"], ["api-url", "--url="]):
            self.assertEqual(run_main(*argv)[:2], (gh.OK, "https://gitlab.example.com/api/v4/projects/group%2Fhub/issues/42\n"))
        code, out, err = run_main("api-url", "--url=https://example.com/x")
        self.assertEqual((code, out), (gh.USAGE, ""))
        self.assertIn("issue の URL ではありません", err)

    def test_api_url_accepts_only_issue_endpoints(self):
        issue = "https://gitlab.example.com/gitlab/api/v4/projects/a%2Fb/issues/7"
        self.assertEqual(run_main("api-url", f"--url={issue}")[:2], (gh.OK, issue + "\n"))
        for url in (issue + "/notes", "https://gitlab.example.com/api/v4/projects/7",
                    "https://gitlab.example.com/?next=/api/v4/projects/7/issues/1"):
            with self.subTest(url=url):
                self.assertEqual(run_main("api-url", f"--url={url}")[:2], (gh.USAGE, ""))

    def test_bad_pairs_are_refused(self):
        self.assertEqual(run_main("fields", "title")[0], gh.USAGE)
        self.assertEqual(run_main("fields", "=x")[0], gh.USAGE)


REQUEST_FIXTURES = HERE / "fixtures" / "requests"


def request_fixture(name: str) -> str:
    return (REQUEST_FIXTURES / f"{name}.txt").read_bytes().decode("utf-8")


class CheckRequestTest(unittest.TestCase):
    def test_all_text_inputs_return_the_same_ascii_json_line(self):
        body = request_fixture("ok-report")
        for args in (
            ["--text", body],
            ["--text-file", str(REQUEST_FIXTURES / "ok-report.txt")],
            ["--", sys.executable, "-c", f"print({body!r}, end='')"],
            ["--change", json.dumps({"record": {"body": body}})],
        ):
            with self.subTest(args=args):
                code, out, err = run_main("check-request", *args)
                self.assertEqual((code, err), (0, ""))
                self.assertTrue(out.isascii())
                self.assertEqual(out.count("\n"), 1)
                self.assertEqual(json.loads(out), gh.check_request(body))

    def test_invalid_and_empty_requests_still_return_json(self):
        for body, route in (("【依頼】集計", "invalid"), ("", "none")):
            with self.subTest(route=route):
                code, out, err = run_main("check-request", "--text", body)
                self.assertEqual((code, err), (0, ""))
                self.assertEqual(json.loads(out)["route"], route)

    def test_wrong_input_is_a_usage_error(self):
        for args in ([], ["--text=x", "--change={}"], ["--change=broken"], ["--change={}"],
                     ['--change={"record":{"body":1}}']):
            with self.subTest(args=args):
                code, out, err = run_main("check-request", *args)
                self.assertEqual((code, out), (gh.USAGE, ""))
                self.assertIn("--change", err)

    def test_command_failure_keeps_the_exit_code_without_output(self):
        code, out, err = run_main("check-request", "--", sys.executable, "-c", "print('途中'); raise SystemExit(5)")
        self.assertEqual((code, out), (5, ""))
        self.assertIn("終了コード 5", err)

    def test_request_fixtures(self):
        self.assertEqual({p.stem for p in REQUEST_FIXTURES.glob("*.txt")},
                         {p.stem for p in REQUEST_FIXTURES.glob("*.json")})
        for expected in sorted(REQUEST_FIXTURES.glob("*.json")):
            with self.subTest(name=expected.stem):
                body = request_fixture(expected.stem)
                result = json.loads(expected.read_text(encoding="utf-8"))
                self.assertEqual(gh.check_request(body), result)
                if result["route"] != "none":
                    # BOM・先頭の空行を含む検証用の本文は、ハブへ書くときの形にそろえる。
                    self.assertRegex(body.removeprefix("\ufeff").lstrip(), "^【依頼】")

    def test_free_text_uses_the_same_normalized_line_numbers(self):
        helper = AGENT / "examples" / "hub_reply.py"
        spec = importlib.util.spec_from_file_location("hub_reply", helper)
        hub_reply = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(hub_reply)
        for name, expected in (
            ("ok-text-line", "\n先月と同じ形でお願いします。\n\n補足です。"),
            ("ok-report", ""),
        ):
            with self.subTest(name=name):
                body = request_fixture(name)
                self.assertEqual(hub_reply.free_text(gh.check_request(body), {"record": {"body": body}}), expected)


class KindsFileTest(unittest.TestCase):
    def test_new_kind_is_added(self):
        path = AGENT / "examples" / "hub-request-kinds.json.example"
        code, out, err = run_main("check-request", f"--kinds-file={path}", "--text=【依頼】[review] 確認\n資料: 月報")
        self.assertEqual((code, err), (0, ""))
        self.assertEqual(json.loads(out)["route"], "review")
        self.assertEqual(set(gh.load_kinds(str(path))), {"report", "inquiry", "account", "review"})

    def test_existing_kind_is_replaced(self):
        replacement = {"title": "別の集計", "text_required": False, "fields": {}}
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "kinds.json"
            path.write_text(json.dumps({"report": replacement}), encoding="utf-8")
            kinds = gh.load_kinds(str(path))
        self.assertEqual(kinds["report"], replacement)
        self.assertTrue(gh.check_request("【依頼】[report] 集計", kinds)["ok"])
        self.assertFalse(gh.check_request("【依頼】[report] 集計")["ok"])

    def test_invalid_file_is_a_usage_error(self):
        definition = {"title": "確認", "text_required": False, "fields": {}}
        bad_files = ["{", "[]", json.dumps({"Report": definition}), json.dumps({"invalid": definition}),
                     json.dumps({"none": definition}), json.dumps({"review": {**definition, "fields": []}}),
                     json.dumps({"review": {**definition, "fields": {"資料": {"pattern": "["}}}}),
                     json.dumps({"review": {**definition, "fields": {"資料": {"required": 1}}}})]
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "kinds.json"
            for raw in [None, *bad_files]:
                with self.subTest(raw=raw):
                    if raw is not None:
                        path.write_text(raw, encoding="utf-8")
                    code, out, err = run_main("check-request", f"--kinds-file={path}", "--text=ふつうのコメント")
                    self.assertEqual((code, out), (gh.USAGE, ""))
                    self.assertTrue(err)


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
        notice = yaml.safe_load((AGENT / "examples" / "change-watch-notice.yaml.example").read_text(encoding="utf-8"))
        notice["state_dir"] = str(self.tmp / "state-notice")                 # ハブとは別の状態
        for src in notice["sources"].values():
            for k in ("schedule", "jitter_sec"):
                src.pop(k)
            src.update(retry={"attempts": 1}, failure_backoff_sec=0)
        self.notice_cfg = self.tmp / "change-watch-notice.yaml"
        self.notice_cfg.write_text(yaml.safe_dump(notice, allow_unicode=True), encoding="utf-8")
        self.conn = self.tmp / "connections.yaml"
        self.conn.write_text("gitlab:\n  - label: default\n    url: ${GITLAB_URL}\n    token: ${GITLAB_TOKEN}\n", encoding="utf-8")
        self.env = {**os.environ, "GITLAB_URL": "http://127.0.0.1:9", "GITLAB_TOKEN": TOKEN,   # 接続の url は使わない
                    "GITLAB_HUB_ISSUE_URL": f"{self.gl.url}/{PROJECT}/-/issues/{ISSUE}"}
        self.paths = {"/home/you/.github/connections.yaml": str(self.conn),
                      "/home/you/.config/change-watch/gitlab-issue-hub-agent/change-watch.yaml": str(self.cfg),
                      "/home/you/.local/share/change-watch/agents/gitlab-issue-hub-agent/gitlab_hub.py": str(PROGRAM),
                      "/home/you/work/hub/on_request.py": str(HANDLER),
                      "/home/you/.config/change-watch/gitlab-issue-hub-agent/change-watch-notice.yaml": str(self.notice_cfg)}
        for name in ("hub-update-note", "hub-description", "hub-description-note", "hub-notice", "hub-notice-expire"):   # call に渡す JSON のファイル
            self.paths[f"/home/you/.config/change-watch/gitlab-issue-hub-agent/{name}.json"] = str(self.tmp / "out" / f"{name}.json")

    def longest_argv(self, run) -> int:
        """run() のあいだに、例のステップに渡したコマンド行のいちばん長いもの（Windows は 8191 文字まで）。"""
        import dagrun
        seen = []
        real = dagrun._argv
        dagrun._argv = lambda *a: seen.append(real(*a)) or seen[-1]
        try:
            run()
        finally:
            dagrun._argv = real
        return max(len(" ".join(argv)) for argv in seen)

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
        done = {"compose": 0, "comment": 0}
        self.assertEqual(self.run_dag(dag, COMMENT="夜間バッチが終わりました\n/close", COMMENT_FILE=""), done)
        self.assertEqual(self.gl.posted, ["夜間バッチが終わりました\n\\/close"])
        self.assertEqual(self.run_dag(dag, COMMENT="", COMMENT_FILE=""), {"compose": 0, "comment": None})   # 空なら書かない
        self.assertEqual(len(self.gl.posted), 1)
        # 長いコメントはファイルで渡す。本文の JSON はコマンド行に載せない（Windows の cmd は 8191 文字まで）。
        long = "夜間バッチの結果です。\n" + "\n".join(f"- 項目 {i}: 正常に終わりました" for i in range(2000))
        source = self.tmp / "comment.txt"
        source.write_text(long, encoding="utf-8")
        import dagrun
        seen = []
        real = dagrun._argv
        self.addCleanup(setattr, dagrun, "_argv", real)
        dagrun._argv = lambda *a: seen.append(real(*a)) or seen[-1]
        self.assertEqual(self.run_dag(dag, COMMENT="", COMMENT_FILE=str(source)), done)
        self.assertEqual(self.gl.posted[-1], long)
        self.assertLess(max(len(" ".join(argv)) for argv in seen), 2000)
        dagrun._argv = real
        self.assertEqual(self.run_dag("change-watch-issue-hub.yaml.example")["get"], 0)
        self.gl.add("あとから付いたコメント")
        self.assertEqual(self.run_dag("change-watch-issue-hub.yaml.example")["request"], None)

    def test_commands_run_through_change_watch_without_config(self):
        """note・description・fields・api-url は change-watch --plugin gitlab_hub.py <コマンド> で動く（設定は読まない）。"""
        def cw(*args):
            return subprocess.run([sys.executable, str(CHANGE_WATCH), "--plugin", str(PROGRAM), *args], capture_output=True,
                                  text=True, encoding="utf-8", env=self.env)
        p = cw("note", "--text=1 行目\n/close")
        self.assertEqual((p.returncode, json.loads(p.stdout)), (0, {"body": "1 行目\n\\/close"}), p.stderr)
        p = cw("description", "--", sys.executable, "-c", "print('出力 --plugin x')")
        self.assertEqual((p.returncode, json.loads(p.stdout)), (0, {"description": "出力 --plugin x"}), p.stderr)
        p = cw("note", "--", sys.executable, "-c", "import sys; sys.exit(5)")
        self.assertEqual((p.returncode, p.stdout), (5, ""))
        p = cw("fields", "title=【完了】題", "add_labels=完了")
        self.assertEqual(json.loads(p.stdout), {"title": "【完了】題", "add_labels": "完了"})
        p = cw("api-url")
        self.assertEqual(p.stdout.strip(), f"{self.gl.url}/api/v4/projects/group%2Fhub/issues/{ISSUE}")
        p = cw("check-request", "--text=【依頼】[report] 月次集計\n対象: 東日本\n期間: 2026-10")
        self.assertEqual((p.returncode, json.loads(p.stdout)["route"]), (0, "report"), p.stderr)
        self.assertFalse((self.tmp / "state").exists())

    def test_route_example(self):
        dag = "examples/change-watch-issue-hub-route.yaml.example"
        example = yaml.safe_load((AGENT / dag).read_text(encoding="utf-8"))
        routes = {"report", "inquiry", "account", "invalid"}
        configured_routes = {p["expected"] for step in example["steps"]
                             for p in step.get("preconditions", []) if p["condition"] == "${CHECK.route}"}
        self.assertLessEqual(set(gh.REQUEST_KINDS), configured_routes)
        self.assertEqual(configured_routes, routes)
        for route in routes:
            script = f"on_{route}.py"
            self.paths[f"/home/you/work/hub/{script}"] = str(AGENT / "examples" / script)

        idle = dict.fromkeys(["check", "commit", *routes, *(f"reply_{route}" for route in routes)])
        idle["get"] = 0
        self.gl.add("【依頼】以前の依頼")
        self.assertEqual(self.run_dag(dag), idle)
        self.assertEqual(self.gl.posted, [])

        cases = (
            ("report", "ok-report", ""),
            ("report", "ok-report", "\ufeff"),
            ("inquiry", "ok-inquiry", ""),
            ("account", "ok-account", ""),
            ("invalid", "invalid-no-kind", ""),
        )
        for route, name, prefix in cases:
            with self.subTest(route=route, prefix=prefix):
                record = self.gl.add(prefix + request_fixture(name))
                self.assertEqual(self.run_dag(dag), {**idle, "check": 0, "commit": 0, route: 0,
                                                     f"reply_{route}": 0})
                posted = self.gl.posted[-1].split("\n")
                self.assertEqual(posted[1], f"元の依頼: note_{record['id']}（alice さん）")
                if route == "invalid":
                    self.assertEqual(posted[0], "【差し戻し】売上の集計")
                    self.assertIn("1 行目に種別がありません", self.gl.posted[-1])
                else:
                    subject = gh.check_request(record["body"])["subject"]
                    self.assertEqual(posted[0], f"【返信】[{route}] {subject}")
                if route == "inquiry":
                    self.assertEqual(posted[2], "受付番号: M20261119093000")
                    self.assertIn("2 行", self.gl.posted[-1])
                # 自分の返信・差し戻しも書式を調べ、返信せずに見終えたと記録する。
                self.assertEqual(self.run_dag(dag), {**idle, "check": 0, "commit": 0})

        self.gl.add("ふつうのコメント")
        self.assertEqual(self.run_dag(dag), {**idle, "check": 0, "commit": 0})
        self.gl.add(request_fixture("ok-report"), system=True)
        self.assertEqual(self.run_dag(dag), {**idle, "commit": 0})
        self.assertEqual(self.run_dag(dag), idle)
        self.assertEqual(len(self.gl.posted), len(cases))

        bad = self.tmp / "bad.py"
        bad.write_text("print('途中'); raise SystemExit(5)\n", encoding="utf-8")
        self.paths["/home/you/work/hub/on_report.py"] = str(bad)
        self.gl.add(request_fixture("ok-report"))
        self.assertEqual(self.run_dag(dag), {**idle, "check": 0, "report": 5, "commit": 0})
        self.assertEqual(self.run_dag(dag), idle)
        self.assertEqual(len(self.gl.posted), len(cases))


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

    def test_description_example(self):
        dag = "examples/change-watch-issue-hub-description.yaml.example"
        done = {"compose": 0, "target": 0, "update": 0, "note": 0, "comment": None}
        self.assertEqual(self.run_dag(dag, DESCRIPTION='## 状況\n"夜間バッチ" 済\n/close', ISSUE_URL="", COMMENT=""), done)   # 指さなければハブ
        self.assertEqual(self.gl.issue["description"], '## 状況\n"夜間バッチ" 済\n\\/close')
        self.assertEqual(self.run_dag(dag, DESCRIPTION="書き換えた説明", ISSUE_URL=f"{self.gl.url}/{PROJECT}/-/issues/{OTHER}", COMMENT=""), done)
        self.assertEqual(self.gl.other["description"], "書き換えた説明")              # URL で指した issue だけが変わる
        self.assertEqual(self.gl.issue["description"], '## 状況\n"夜間バッチ" 済\n\\/close')
        self.assertEqual(self.gl.posted, [])                                         # COMMENT が空ならコメントしない
        # 説明だけの変更では GitLab がメールを送らないので、COMMENT を渡すと書き換えた issue にコメントを足す。
        told = {**done, "comment": 0}
        self.assertEqual(self.run_dag(dag, DESCRIPTION="二度目", ISSUE_URL="", COMMENT="説明を更新しました"), told)
        self.assertEqual(self.gl.posted, ["説明を更新しました"])
        self.assertEqual(self.run_dag(dag, DESCRIPTION="三度目", ISSUE_URL=f"{self.gl.url}/{PROJECT}/-/issues/{OTHER}",
                                      COMMENT="説明を更新しました"), told)
        self.assertEqual((self.gl.other_posted, len(self.gl.posted)), (["説明を更新しました"], 1))
        self.assertEqual(self.run_dag(dag, DESCRIPTION="", ISSUE_URL="", COMMENT="説明なし")["comment"], None)   # 空なら消さず、コメントもしない
        self.assertEqual(self.run_dag(dag, DESCRIPTION="x", ISSUE_URL="https://example.com/not-an-issue", COMMENT="")["target"], gh.USAGE)
        long = "\n".join(f"- 項目 {i}: 正常に終わりました" for i in range(2000))          # 長い説明も、コマンド行に載せずに書ける
        compose = self.tmp / "out" / "hub-description.json"
        self.assertEqual(run_main("description", f"--text={long}", "--out", str(compose))[:2], (0, f"{compose}\n"))
        self.assertEqual(json.loads(compose.read_text(encoding="utf-8")), {"description": long})
        self.assertEqual(run_main("fields", "title=【完了】", "--out", str(compose))[0], 0)
        self.assertEqual(json.loads(compose.read_text(encoding="utf-8")), {"title": "【完了】"})
        self.assertEqual(run_main("note", "--text=", "--out", str(self.tmp / "out" / "none.json")), (0, "", ""))   # 空なら書かない
        self.assertFalse((self.tmp / "out" / "none.json").exists())
        self.assertEqual(run_main("note", "--text=a", "--text-file", str(compose))[0], gh.USAGE)   # 2 つは渡せない
        self.assertEqual(len(self.gl.updates), 2)
        self.assertEqual(len(self.gl.posted), 1)

    def test_prune_example_deletes_only_own_old_comments(self):
        dag = "examples/change-watch-issue-hub-prune.yaml.example"
        now = datetime.datetime.now(datetime.timezone.utc)
        mine = self.gl.add("古い知らせ", author=BOT)                                  # 2026-11-09（7 日より古い）
        self.gl.add("人のコメント")
        self.gl.add("changed the description", author=BOT, system=True)
        self.gl.add("昨日の知らせ", author=BOT, created_at=(now - datetime.timedelta(days=1)).isoformat())
        last = self.gl.add("そのあとの古い日付のコメント", author=BOT)                 # 待っているコメントの後ろは見ない
        hub = "change-watch-issue-hub.yaml.example"
        self.assertEqual(self.run_dag(hub)["get"], 0)                                # 依頼を見るワークフローも動いている
        run = lambda **kw: self.run_dag(dag, **{"AUTHOR": "hub-bot", "KEEP_DAYS": "7", **kw})
        self.assertEqual(run(), {"get": 0, "check": 0, "delete": 0, "commit": 0})    # 初回から、今あるコメントも見る
        self.assertEqual(self.gl.deleted, [mine["id"]])
        keep = {"get": 0, "check": 0, "delete": None, "commit": 0}
        self.assertEqual([run(), run(), run()], [keep] * 3)                          # 消えたことを覚え、人のコメントと記録は消さない
        wait = {"get": 0, "check": 0, "delete": None, "commit": None}
        self.assertEqual([run(), run()], [wait, wait])                               # まだ新しいコメントは、消さずに次も見る
        self.assertEqual(self.gl.deleted, [mine["id"]])
        self.assertEqual(run(KEEP_DAYS="0")["check"], gh.USAGE)                      # すぐ消す指定は受けない
        self.assertEqual(run(AUTHOR="")["check"], gh.USAGE)
        self.assertEqual(self.gl.deleted, [mine["id"]])
        with self.gl.lock:                                                           # 日がたてば消し、後ろへ進む
            self.gl.notes[-2]["created_at"] = "2026-11-09T09:00:00.000+09:00"
            self.gl.notes[-2]["body"] += "（書き直し）"
        self.assertEqual([run()["delete"] for _ in range(4)], [0, None, 0, None])
        self.assertEqual(len(self.gl.deleted), 3)
        self.assertEqual(self.gl.deleted[-1], last["id"])
        self.assertEqual(run(), {"get": 0, "check": 0, "delete": None, "commit": None})   # 消し終えたら、もう何もしない
        self.assertEqual([n["body"] for n in self.gl.notes], ["人のコメント", "changed the description"])
        # 依頼を見るワークフローは、消えたコメントには返信せず、見終えたと記録する。
        runs = [self.run_dag(hub) for _ in range(4)]
        self.assertEqual(runs[0], {"get": 0, "request": None, "reply": None, "commit": 0})
        self.assertEqual(runs[-1]["commit"], None)
        self.assertEqual(self.gl.posted, [])

    def test_old_note_decision(self):
        now = datetime.datetime(2026, 11, 21, tzinfo=datetime.timezone.utc)
        rec = {"system": False, "author": {"username": "hub-bot"}, "created_at": "2026-11-13T23:59:59.000Z"}
        decide = lambda record, kind="appeared", days=7: gh.old_note({"kind": kind, "record": record}, "hub-bot", days, now)
        self.assertEqual(decide(rec), "delete")
        self.assertEqual(decide(rec, kind="changed"), "delete")
        self.assertEqual(decide({**rec, "created_at": "2026-11-14T00:00:00Z"}), "wait")          # ちょうど 7 日はまだ消さない
        self.assertEqual(decide({**rec, "created_at": "2026-11-14T08:59:59+09:00"}), "delete")   # 時差つきの日時
        self.assertEqual(decide(rec, kind="removed"), "keep")
        self.assertEqual(decide({**rec, "system": True}), "keep")
        self.assertEqual(decide({**rec, "author": {"username": "alice"}}), "keep")
        self.assertEqual(decide({**rec, "created_at": "いつか"}), "keep")                         # 読めない日時は消さない
        self.assertEqual(decide(None), "keep")
        self.assertEqual(run_main("check-old-note", "--change=", "--author=hub-bot"), (0, "", ""))
        self.assertEqual(run_main("check-old-note", "--change=[]", "--author=hub-bot")[0], gh.USAGE)

    def test_notice_example_opens_and_closes_a_new_issue(self):
        dag = "examples/change-watch-issue-hub-notice.yaml.example"
        self.env["GITLAB_NOTICE_PROJECT_URL"] = f"{self.gl.url}/{PROJECT}"
        run = lambda **kw: self.run_dag(dag, **{"TITLE": "", "TEXT": "", "TEXT_FILE": "", "LABELS": "通知", "CLOSE": "yes", **kw})
        self.assertEqual(run(TITLE="夜間バッチ", TEXT="終わりました\n/close"), {"compose": 0, "create": 0, "close": 0})
        self.assertEqual({k: v for k, v in self.gl.created[100].items() if k != "created_at"}, {"iid": 100, "state": "closed", "labels": ["通知"],
                                                "title": "夜間バッチ", "description": "終わりました\n\\/close"})
        self.assertEqual((self.gl.posted, self.gl.updates), ([], []))                 # ハブの issue には書かない
        long = "\n".join(f"- 項目 {i}: 正常に終わりました" for i in range(2000))          # 長い本文もファイルで渡せる
        source = self.tmp / "notice.txt"
        source.write_text("集計の結果\n" + long, encoding="cp932")
        self.assertEqual(run(TEXT_FILE=str(source), LABELS="", CLOSE="no"), {"compose": 0, "create": 0, "close": None})
        self.assertEqual({k: self.gl.created[101][k] for k in ("title", "state", "labels")}, {"title": "集計の結果",
                                                                                            "state": "opened", "labels": []})
        self.assertEqual(self.gl.created[101]["description"], "集計の結果\n" + long)
        self.assertEqual(run(), {"compose": 0, "create": None, "close": None})       # 本文が無ければ立てない
        self.assertEqual(run(TEXT="化け\ufffd")["compose"], gh.USAGE)                # 文字化けは立てない
        self.assertEqual(len(self.gl.created), 2)

    def test_notice_expire_example_closes_or_deletes_old_issues(self):
        dag = "examples/change-watch-issue-hub-notice-expire.yaml.example"
        self.env["GITLAB_NOTICE_PROJECT_URL"] = f"{self.gl.url}/{PROJECT}"
        run = lambda **kw: self.run_dag(dag, **{"TITLE": "", "TEXT": "", "TEXT_FILE": "", "LABELS": "通知",
                                                "KEEP_DAYS": "7", "MODE": "close", **kw})
        steps = ("compose", "create", "get", "check", "close", "delete", "commit")
        result = lambda *codes: dict(zip(steps, codes))
        now = datetime.datetime.now(datetime.timezone.utc)
        ago = lambda days: (now - datetime.timedelta(days=days)).isoformat()
        # 知らせを届けると issue を立て、開いたまま残す。片付けは同じ起動で、まだ新しいので待つ。
        self.assertEqual(run(TITLE="夜間バッチ", TEXT="終わりました"), result(0, 0, 0, 0, None, None, None))
        self.assertEqual({k: self.gl.created[100][k] for k in ("title", "state", "labels")},
                         {"title": "夜間バッチ", "state": "opened", "labels": ["通知"]})
        self.assertEqual(run(), result(0, None, 0, 0, None, None, None))            # 予定で動けば、片付けだけ（まだ待つ）
        self.assertEqual(run(TEXT="二件目"), result(0, 0, 0, 0, None, None, None))
        with self.gl.lock:                                                         # 日がたつ
            self.gl.created[100]["created_at"] = ago(8)
            self.gl.created[101]["created_at"] = ago(3)
        self.assertEqual(run(KEEP_DAYS="0")["check"], gh.USAGE)                     # すぐ片付ける指定は受けない
        self.assertEqual(run(), result(0, None, 0, 0, 0, None, 0))                 # 8 日前の issue だけ閉じる
        self.assertEqual([self.gl.created[i]["state"] for i in (100, 101)], ["closed", "opened"])
        self.assertEqual(run()["commit"], 0)                                       # 閉じた issue が一覧から外れたと記録する
        self.assertEqual([run()["commit"], run()["commit"]], [None, None])         # 3 日前の issue は待つ
        self.assertEqual(run(MODE="delete")["delete"], None)                       # 待っている issue は消さない
        with self.gl.lock:
            self.gl.created[101]["created_at"] = ago(10)
        self.assertEqual(run(MODE="delete"), result(0, None, 0, 0, None, 0, 0))    # 消すなら消す
        self.assertEqual((self.gl.removed, sorted(self.gl.created)), ([101], [100]))
        self.assertEqual([run()["commit"] for _ in range(3)][-1], None)            # 片付け終えたら、もう何もしない
        self.assertEqual(len(self.gl.created), 1)
        self.assertEqual((self.gl.posted, self.gl.updates), ([], []))              # ハブの issue には書かない
        # 長い知らせでも、片付けのステップには説明を渡さない（コマンド行に収まらなくなる）。
        long = self.tmp / "long.txt"
        long.write_text("\n".join(f"- 項目 {i}: 正常に終わりました" for i in range(2000)), encoding="utf-8")
        self.assertLess(self.longest_argv(lambda: run(TEXT_FILE=str(long))), 1000)
        with self.gl.lock:
            self.gl.created[max(self.gl.created)]["created_at"] = ago(9)
        codes = {}
        self.assertLess(self.longest_argv(lambda: codes.update(run())), 1000)
        self.assertEqual(codes, result(0, None, 0, 0, 0, None, 0))                 # 長い説明の issue も閉じる

    def test_prune_passes_only_the_fields_it_needs(self):
        dag = "examples/change-watch-issue-hub-prune.yaml.example"
        self.gl.add("\n".join(f"- 項目 {i}: 正常に終わりました" for i in range(2000)), author=BOT)   # 長い知らせ（2026-11-09）
        codes = {}
        self.assertLess(self.longest_argv(lambda: codes.update(self.run_dag(dag, AUTHOR="hub-bot", KEEP_DAYS="7"))), 1000)
        self.assertEqual(codes, {"get": 0, "check": 0, "delete": 0, "commit": 0})
        self.assertEqual(self.gl.notes, [])

    def test_check_commands_take_fields(self):
        args = ("--state=opened", "--created-at=2020-01-01T00:00:00Z")
        self.assertEqual(run_main("check-old-issue", "--kind=appeared", *args), (0, "expire\n", ""))
        self.assertEqual(run_main("check-old-issue", "--kind=removed", *args), (0, "keep\n", ""))
        for empty in ("", "${CHANGE.kind}"):                                           # 受け取った変化が無ければ何も出さない
            self.assertEqual(run_main("check-old-issue", f"--kind={empty}", *args), (0, "", ""))
            self.assertEqual(run_main("check-old-note", f"--kind={empty}", "--author=hub-bot"), (0, "", ""))
        note = ("--author=hub-bot", "--writer=hub-bot", "--created-at=2020-01-01T00:00:00Z")
        self.assertEqual(run_main("check-old-note", "--kind=appeared", "--system=false", *note), (0, "delete\n", ""))
        self.assertEqual(run_main("check-old-note", "--kind=appeared", "--system=true", *note), (0, "keep\n", ""))
        self.assertEqual(run_main("check-old-note", "--kind=appeared", "--system=false", "--author=hub-bot",
                                  "--writer=alice", "--created-at=2020-01-01T00:00:00Z"), (0, "keep\n", ""))

    def test_old_issue_decision(self):
        now = datetime.datetime(2026, 11, 21, tzinfo=datetime.timezone.utc)
        rec = {"state": "opened", "created_at": "2026-11-13T23:59:59.000Z"}
        decide = lambda record, kind="appeared", days=7: gh.old_issue({"kind": kind, "record": record}, days, now)
        self.assertEqual(decide(rec), "expire")
        self.assertEqual(decide({**rec, "created_at": "2026-11-14T00:00:00Z"}), "wait")   # ちょうど 7 日はまだ
        self.assertEqual(decide({**rec, "created_at": "2026-11-14T08:59:59+09:00"}), "expire")
        self.assertEqual(decide({**rec, "state": "closed"}), "keep")
        self.assertEqual(decide(rec, kind="removed"), "keep")
        self.assertEqual(decide({**rec, "created_at": "いつか"}), "keep")
        self.assertEqual(run_main("check-old-issue", "--change="), (0, "", ""))
        self.assertEqual(run_main("check-old-issue", "--change=[]")[0], gh.USAGE)
        self.assertEqual(run_main("check-old-issue", "--change={}", "--days=x")[0], gh.USAGE)

    def test_project_api_url(self):
        api = "https://gitlab.example.com/api/v4/projects/group%2Fsub%2Fnotices"
        for url in ("https://gitlab.example.com/group/sub/notices", "https://gitlab.example.com/group/sub/notices/",
                    "https://gitlab.example.com/group/sub/notices/-/issues/7", "https://gitlab.example.com/group/sub/notices/-/issues",
                    "https://gitlab.example.com/group/sub/notices?tab=1", api + "/issues/3"):
            with self.subTest(url=url):
                self.assertEqual(gh.gitlab_project_api(url), api)
        for bad in ("", "https://gitlab.example.com/", "notices"):
            with self.subTest(bad=bad), self.assertRaises(ValueError):
                gh.gitlab_project_api(bad)
        code, out, _ = run_main("issue", "--text=\n\n  一行目  \n二行目", "--labels= ")
        self.assertEqual((code, json.loads(out)), (0, {"title": "一行目", "description": "一行目  \n二行目"}))
        self.assertEqual(json.loads(run_main("issue", "--title= 題\nの続き ", "--text=x" )[1])["title"], "題 の続き")
        self.assertEqual(run_main("issue", "--title=題", "--text= "), (0, "", ""))

    def test_maintenance_runs(self):
        self.assertEqual(self.run_dag("change-watch-gitlab-issue-hub-agent-maintenance.yaml.example"), {"gc": 0})


if __name__ == "__main__":
    unittest.main()
