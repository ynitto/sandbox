"""gitlab-mr-review-agent のテスト（標準ライブラリと PyYAML。`python -m unittest discover -s test`）。

gitlab_mr.py のフィルタ・チェック・エージェントが使うコマンドと、設定の例（change-watch.yaml.example・prompt.md.example）を
そのまま本物の change-watch で Dagu のワークフローと同じ順（fetch → evaluate → claim → render → 処理 → finish --exit-code）に
回したときの流れを確かめる。GitLab は手元の HTTP サーバで偽装し、kiro-cli（Dagu の harness.run）の代わりに、テストが
gitlab_mr.py のコマンドを呼ぶ。
"""
from __future__ import annotations

import importlib.util
import io
import json
import os
import shutil
import subprocess
import sys
import tempfile
import threading
import unittest
from contextlib import redirect_stderr, redirect_stdout
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from unittest import mock
from urllib.parse import parse_qs, unquote, urlparse

try:
    import yaml
except ImportError:      # pragma: no cover
    yaml = None

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
PLUGIN = ROOT / "gitlab_mr.py"
CHANGE_WATCH = ROOT.parents[1] / "change_watch.py"   # tools/change-watch/agents/<エージェント>
spec = importlib.util.spec_from_file_location("gitlab_mr", PLUGIN)
gm = importlib.util.module_from_spec(spec)
spec.loader.exec_module(gm)

TOKEN = "t0ken"
PROJECT = "g/p"

DIFF_A = "@@ -1,3 +1,4 @@\n def f(x):\n-    return x\n+    y = x * 2\n+    return y\n \n"
#          old: 1 def / 2 return x(-) / 3 空行     new: 1 def / 2 y=(+) / 3 return y(+) / 4 空行
DIFF_B = "@@ -1,3 +1,4 @@\n def f(x):\n-    return x\n+    y = (x or 0) * 2\n+    return y\n \n"     # 指摘を直した
FIX = "@@ -2,1 +2,1 @@\n-    y = x * 2\n+    y = (x or 0) * 2\n"                                  # 前の head からの差分


def make_mr(iid: int, sha: str = "a" * 40, **kw) -> dict:
    base = {"iid": iid, "project_id": 7, "title": f"MR {iid}", "state": "opened", "draft": False,
            "target_branch": "main", "source_branch": f"feature-{iid}", "sha": sha, "description": "説明",
            "author": {"username": "alice"}, "user_notes_count": 0, "updated_at": "2026-11-01T00:00:00Z",
            "web_url": f"https://gl.example/g/p/-/merge_requests/{iid}",
            "diff_refs": {"base_sha": "b" * 40, "start_sha": "b" * 40, "head_sha": sha}}
    base.update(kw)
    return base


class FakeGitLab:
    """MR の一覧・1 件・diffs・compare・discussions・notes・ファイル。world を書き換えて GitLab の状態を変える。"""

    def __init__(self):
        self.mrs: dict[str, dict] = {}
        self.diffs: dict[str, list] = {}          # iid → MR 全体の差分
        self.compares: dict[tuple, list] = {}     # (from, to) → 差分
        self.files: dict[tuple, str] = {}         # (path, ref) → 中身
        self.discussions: dict[str, list] = {}
        self.notes: dict[str, list] = {}
        self.writes: list[tuple] = []
        self.requests: list[str] = []
        self.project = {"default_branch": "main", "http_url_to_repo": ""}
        self.reject_positions = False
        self.lock = threading.Lock()
        self._seq = 0
        outer = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *a):
                pass

            def _route(self, method):
                u = urlparse(self.path)
                if self.headers.get("PRIVATE-TOKEN") != TOKEN:
                    return self._send(401, {"message": "401"})
                n = int(self.headers.get("Content-Length") or 0)
                body = json.loads(self.rfile.read(n) or b"null") if n else None
                q = {k: v[0] for k, v in parse_qs(u.query).items()}
                prefix = "/api/v4/projects/"
                if not u.path.startswith(prefix):
                    return self._send(404, {})
                rest = u.path[len(prefix):]
                proj, _, tail = rest.partition("/")
                if unquote(proj) not in (PROJECT, "7"):
                    return self._send(404, {"message": "project"})
                with outer.lock:
                    return self._handle(method, tail, q, body)

            def _handle(self, method, tail, q, body):
                parts = tail.split("/")
                outer.requests.append(f"{method} /{tail}")
                if tail == "":
                    return self._send(200, outer.project)
                if parts[0] == "repository" and parts[1] == "compare":
                    d = outer.compares.get((q.get("from"), q.get("to")))
                    return self._send(200, {"diffs": d}) if d is not None else self._send(404, {"message": "ref"})
                if parts[0] == "repository" and parts[1] == "files":
                    text = outer.files.get((unquote(parts[2]), q.get("ref")))
                    return self._send_raw(text) if text is not None else self._send(404, {"message": "file"})
                if parts[0] != "merge_requests":
                    return self._send(404, {})
                if len(parts) == 1:
                    items = [m for m in sorted(outer.mrs.values(), key=lambda m: m["iid"])
                             if q.get("state") != "opened" or m["state"] == "opened"]
                    return self._send(200, items)
                iid = parts[1]
                if iid not in outer.mrs:
                    return self._send(404, {"message": "404 Not found"})
                if len(parts) == 2:
                    return self._send(200, outer.mrs[iid])
                kind = parts[2]
                if kind == "diffs":
                    return self._send(200, outer.diffs.get(iid, []))
                if kind == "notes" and method == "GET":
                    return self._send(200, list(reversed(outer.notes.get(iid, []))))
                if kind == "notes" and method == "POST":
                    outer.writes.append(("note", iid, body["body"]))
                    outer.notes.setdefault(iid, []).append({"id": outer._id(), "body": body["body"],
                                                            "author": {"username": "bot"}})
                    return self._send(201, outer.notes[iid][-1])
                if kind == "discussions" and len(parts) == 3 and method == "GET":
                    return self._send(200, outer.discussions.get(iid, []))
                if kind == "discussions" and len(parts) == 3 and method == "POST":
                    if body.get("position") and outer.reject_positions:
                        return self._send(400, {"message": "line_code can't be blank"})
                    did = f"d{outer._id()}"
                    outer.writes.append(("discussion", iid, body))
                    outer.discussions.setdefault(iid, []).append(
                        {"id": did, "notes": [{"id": outer._id(), "body": body["body"], "author": {"username": "bot"},
                                               "resolvable": True, "resolved": False,
                                               "position": body.get("position")}]})
                    return self._send(201, outer.discussions[iid][-1])
                d = next((x for x in outer.discussions.get(iid, []) if x["id"] == parts[3]), None)
                if d is None:
                    return self._send(404, {"message": "discussion"})
                if len(parts) == 5 and parts[4] == "notes" and method == "POST":
                    outer.writes.append(("reply", iid, parts[3], body["body"]))
                    d["notes"].append({"id": outer._id(), "body": body["body"], "author": {"username": "bot"},
                                       "resolvable": True, "resolved": False})
                    return self._send(201, d["notes"][-1])
                if len(parts) == 4 and method == "PUT":
                    outer.writes.append(("resolve", iid, parts[3]))
                    for n in d["notes"]:
                        n["resolved"] = q.get("resolved") == "true"
                    return self._send(200, d)
                return self._send(404, {})

            def do_GET(self):
                self._route("GET")

            def do_POST(self):
                self._route("POST")

            def do_PUT(self):
                self._route("PUT")

            def _send(self, code, body):
                raw = json.dumps(body).encode()
                self.send_response(code)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(raw)))
                self.end_headers()
                self.wfile.write(raw)

            def _send_raw(self, text):
                raw = text.encode()
                self.send_response(200)
                self.send_header("Content-Type", "text/plain")
                self.send_header("Content-Length", str(len(raw)))
                self.end_headers()
                self.wfile.write(raw)

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        threading.Thread(target=self.server.serve_forever, daemon=True).start()
        self.url = f"http://127.0.0.1:{self.server.server_address[1]}"

    def _id(self) -> int:
        self._seq += 1
        return self._seq

    def put(self, mr: dict, diff: str = DIFF_A, path: str = "app.py"):
        with self.lock:
            self.mrs[str(mr["iid"])] = mr
            self.diffs[str(mr["iid"])] = [{"old_path": path, "new_path": path, "diff": diff}]

    def push(self, iid: int, sha: str, diff: str = DIFF_B, path: str = "app.py", since_diff: str | None = None,
             extra: list | None = None):
        """コミットを積む（head が変わる）。diff は MR 全体の差分。since_diff があれば、前の head からの compare も返す
        （extra はその compare に混ぜるほかのファイル。リベースで入った対象ブランチの変更）。"""
        with self.lock:
            mr = self.mrs[str(iid)]
            old = mr["sha"]
            mr["sha"] = sha
            mr["diff_refs"] = dict(mr["diff_refs"], head_sha=sha)
            self.diffs[str(iid)] = [{"old_path": path, "new_path": path, "diff": diff}]
            if since_diff is not None:
                self.compares[(old, sha)] = [{"old_path": path, "new_path": path, "diff": since_diff}] + list(extra or [])

    def reply(self, iid: int, did: str, who: str, body: str):
        with self.lock:
            d = next(x for x in self.discussions[str(iid)] if x["id"] == did)
            d["notes"].append({"id": self._id(), "body": body, "author": {"username": who}, "resolvable": True,
                               "resolved": False})

    def close(self):
        self.server.shutdown()
        self.server.server_close()


class _GitLabBase(unittest.TestCase):
    def setUp(self):
        self.gl = FakeGitLab()
        self.addCleanup(self.gl.close)
        self.env = {"GITLAB_URL": self.gl.url, "GITLAB_TOKEN": TOKEN, "GITLAB_PROJECT": PROJECT}

    def cli(self, *args) -> tuple[int, str]:
        buf = io.StringIO()
        with mock.patch.dict(os.environ, self.env), redirect_stdout(buf):
            code = gm.main([str(a) for a in args])
        return code, buf.getvalue()


# ---------------------------------------------------------------------------
# フィルタ・差分・印
# ---------------------------------------------------------------------------

class UnitTest(unittest.TestCase):
    def test_since_and_kind(self):
        ev = {"record": make_mr(1), "refreshed": {"last_handled": {"sha": "c" * 40, "draft": False}}}
        self.assertEqual(gm.gitlab_mr_since(ev), "c" * 40)
        self.assertEqual(gm.gitlab_mr_kind(ev), "再レビュー（前回は cccccccc）")
        ev = {"record": make_mr(1), "refreshed": {"last_handled": None}}
        self.assertEqual(gm.gitlab_mr_since(ev), "")
        self.assertEqual(gm.gitlab_mr_kind(ev), "初めてのレビュー")
        with self.assertRaises(ValueError):
            gm.gitlab_mr_since("x")

    def test_new_lines_and_position(self):
        self.assertEqual(gm.new_lines(DIFF_A), {1: 1, 2: None, 3: None, 4: 3})
        mr = make_mr(1)
        diffs = [{"old_path": "app.py", "new_path": "app.py", "diff": DIFF_A}]
        pos = gm.position_for(mr, diffs, "app.py", 2)
        self.assertEqual((pos["new_line"], "old_line" in pos, pos["head_sha"]), (2, False, "a" * 40))
        self.assertEqual(gm.position_for(mr, diffs, "app.py", 4)["old_line"], 3)    # 前後の行は両方の番号が要る
        self.assertIsNone(gm.position_for(mr, diffs, "app.py", 30))                  # 差分に無い行
        self.assertIsNone(gm.position_for(mr, diffs, "other.py", 1))

    def test_own_threads_reads_marks(self):
        mark = gm.finding_mark("k1", "app.py", 3)
        ds = [{"id": "d1", "notes": [{"body": f"直して\n\n{mark}", "resolvable": True, "resolved": False},
                                     {"body": "意図どおりです", "author": {"username": "alice"}, "resolvable": True,
                                      "resolved": False}]},
              {"id": "d2", "notes": [{"body": "人のコメント", "resolvable": True, "resolved": False}]}]
        threads = gm.own_threads(ds)
        self.assertEqual(len(threads), 1)
        t = threads[0]
        self.assertEqual((t["id"], t["key"], t["path"], t["line"], t["body"], t["resolved"]),
                         ("d1", "k1", "app.py", "3", "直して", False))
        self.assertEqual(t["replies"], [{"author": "alice", "body": "意図どおりです"}])

    def test_mark_survives_awkward_paths(self):
        mark = gm.finding_mark("k2", 'a/b>c"d.py', 7)
        t = gm.own_threads([{"id": "d1", "notes": [{"body": f"x\n\n{mark}"}]}])[0]
        self.assertEqual((t["key"], t["path"], t["line"]), ("k2", "a/bcd.py", "7"))
        self.assertEqual(t["path"], gm.mark_path('a/b>c"d.py'))

    def test_iid_accepts_bang_and_hash(self):
        self.assertEqual((gm._iid("12"), gm._iid("!12"), gm._iid(" #12 ")), ("12", "12", "12"))
        with self.assertRaises(Exception):
            gm._iid("abc")

    def test_fingerprint_ignores_rebase_but_not_changes(self):
        a = [{"old_path": "app.py", "new_path": "app.py", "diff": DIFF_A}]
        moved = [{"old_path": "app.py", "new_path": "app.py",
                  "diff": DIFF_A.replace("@@ -1,3 +1,4 @@", "@@ -10,3 +10,4 @@").replace(" def f(x):", " def f(x):  # 前後の行")}]
        self.assertEqual(gm.diff_fingerprint(a, "a" * 40), gm.diff_fingerprint(moved, "c" * 40))   # リベースで行がずれただけ
        self.assertNotEqual(gm.diff_fingerprint(a, "a" * 40),
                            gm.diff_fingerprint([{"old_path": "app.py", "new_path": "app.py", "diff": DIFF_B}], "a" * 40))
        self.assertNotEqual(gm.diff_fingerprint(a, "a" * 40), gm.diff_fingerprint(a + [{"old_path": "x", "new_path": "y",
                                                                                      "renamed_file": True, "diff": ""}], "a" * 40))
        big = [{"old_path": "big.json", "new_path": "big.json", "diff": "", "too_large": True}]
        self.assertNotEqual(gm.diff_fingerprint(big, "a" * 40), gm.diff_fingerprint(big, "c" * 40))  # 中身が見えなければ head で

    def test_reviewed_matches_diff_or_old_mark(self):
        fp = gm.diff_fingerprint([{"new_path": "a", "diff": DIFF_A}], "a" * 40)
        notes = [{"body": f"まとめ\n<!-- mr-review-agent:summary sha={'a' * 40} diff={fp} -->"}]
        self.assertEqual(gm.reviewed(notes, "c" * 40, fp), "a" * 40)
        self.assertIsNone(gm.reviewed(notes, "a" * 40, "0" * 16))
        old = [{"body": f"<!-- mr-review-agent:summary sha={'a' * 40} -->"}]
        self.assertEqual(gm.reviewed(old, "a" * 40, fp), "a" * 40)
        self.assertIsNone(gm.reviewed(old, "c" * 40, fp))

    def test_long_diff_lists_names(self):
        diffs = [{"old_path": f"f{i}.py", "new_path": f"f{i}.py", "diff": "x" * 100} for i in range(5)]
        out = gm.render_diffs(diffs, limit=300)
        self.assertIn("--- f0.py", out)
        self.assertIn("- f4.py", out)


class FakeCtx:
    def __init__(self, record, now):
        self.event = {"record": record}
        self.options, self.conn = {}, {"url": "https://gl.example"}
        self.now, self.calls, self.logs = now, [], []

    def get_json(self, url, headers=None, query=None):
        self.calls.append(url)
        return self.now

    def log(self, msg):
        self.logs.append(msg)


class ChangedDuringReviewTest(unittest.TestCase):
    def test_push_during_review(self):
        ctx = FakeCtx(make_mr(3), make_mr(3, sha="c" * 40))
        self.assertTrue(gm.changed_during_review(ctx))
        self.assertEqual(ctx.calls, ["https://gl.example/api/v4/projects/7/merge_requests/3"])

    def test_only_comments(self):
        self.assertFalse(gm.changed_during_review(FakeCtx(make_mr(3), make_mr(3, user_notes_count=4))))
        self.assertTrue(gm.changed_during_review(FakeCtx(make_mr(3), make_mr(3, draft=True))))


class PagedCtx(FakeCtx):
    """URL ごとに返す値を決める（diffs・notes はページで返す）。"""

    def __init__(self, record, diffs, notes):
        super().__init__(record, None)
        self.diffs, self.notes = diffs, notes

    def get_json(self, url, headers=None, query=None):
        self.calls.append(url)
        page = int((query or {}).get("page", "1"))
        items = self.diffs if url.endswith("/diffs") else self.notes if url.endswith("/notes") else []
        return items[(page - 1) * 100: page * 100]


class AlreadyReviewedTest(unittest.TestCase):
    def test_rebased_mr_is_skipped(self):
        diffs = [{"old_path": "app.py", "new_path": "app.py", "diff": DIFF_A}]
        fp = gm.diff_fingerprint(diffs, "a" * 40)
        notes = [{"body": "人のコメント"}] * 150 + [{"body": f"<!-- mr-review-agent:summary sha={'a' * 40} diff={fp} -->"}]
        ctx = PagedCtx(make_mr(3, sha="c" * 40), diffs, notes)
        self.assertTrue(gm.already_reviewed(ctx))                        # 2 ページ目のまとめも見る
        self.assertIn("リベースなど", ctx.logs[0])

    def test_changed_content_runs(self):
        fp = gm.diff_fingerprint([{"new_path": "app.py", "diff": DIFF_A}], "a" * 40)
        notes = [{"body": f"<!-- mr-review-agent:summary sha={'a' * 40} diff={fp} -->"}]
        ctx = PagedCtx(make_mr(3, sha="c" * 40), [{"old_path": "app.py", "new_path": "app.py", "diff": DIFF_B}], notes)
        self.assertFalse(gm.already_reviewed(ctx))


# ---------------------------------------------------------------------------
# エージェントが使うコマンド
# ---------------------------------------------------------------------------

class CommandTest(_GitLabBase):
    def setUp(self):
        super().setUp()
        self.gl.put(make_mr(1))

    def test_comment_on_diff_line_once(self):
        code, out = self.cli("comment", 1, "--path", "app.py", "--line", 2, "--body", "x が None のとき落ちます")
        self.assertEqual(code, 0, out)
        kind, _, body = self.gl.writes[-1]
        self.assertEqual(body["position"]["new_line"], 2)
        self.assertIn("mr-review-agent:finding", body["body"])
        code, out = self.cli("comment", 1, "--path", "app.py", "--line", 2, "--body", "x が None のとき落ちます")
        self.assertIn("同じ指摘が既にあります", out)
        self.assertEqual(len(self.gl.discussions["1"]), 1)            # やり直しても重ならない

    def test_reworded_finding_on_same_line_is_not_added(self):
        self.cli("comment", 1, "--path", "app.py", "--line", 2, "--body", "x が None のとき落ちます")
        code, out = self.cli("comment", 1, "--path", "app.py", "--line", 2, "--body", "None を渡すと例外になります")
        self.assertIn("未解決の指摘", out)
        self.assertEqual(len(self.gl.discussions["1"]), 1)
        self.cli("resolve", 1, self.gl.discussions["1"][0]["id"], "--body", "意図どおりとのことなので閉じます")
        code, out = self.cli("context", 1)
        self.assertIn("### 解決済みの指摘（同じ内容をもう一度指摘しない）", out)
        self.assertIn("- app.py:2  x が None のとき落ちます", out)

    def test_context_after_rebase_shows_only_mr_files(self):
        self.gl.push(1, "c" * 40, since_diff=FIX,
                     extra=[{"old_path": "main_only.py", "new_path": "main_only.py", "diff": "@@ -1 +1 @@\n-a\n+b\n"}])
        code, out = self.cli("context", 1, "--since", "a" * 40)
        self.assertIn("+    y = (x or 0) * 2", out)
        self.assertNotIn("main_only.py", out)                            # 対象ブランチから入った変更は出さない

    def test_comment_outside_diff_becomes_mr_thread(self):
        code, out = self.cli("comment", 1, "--path", "lib/util.py", "--line", 10, "--body", "こちらも直す")
        self.assertEqual(code, 0, out)
        body = self.gl.writes[-1][2]
        self.assertNotIn("position", body)
        self.assertIn("`lib/util.py:10`", body["body"])
        self.gl.reject_positions = True                                # GitLab が行を受け付けないときも同じ
        code, out = self.cli("comment", 1, "--path", "app.py", "--line", 3, "--body", "別の指摘")
        self.assertEqual(code, 0, out)
        self.assertIn("MR 全体のスレッド", out)

    def test_resolve_only_own_threads(self):
        self.cli("comment", 1, "--path", "app.py", "--line", 2, "--body", "直して")
        did = self.gl.discussions["1"][0]["id"]
        self.gl.discussions["1"].append({"id": "human", "notes": [{"body": "人の指摘", "resolvable": True,
                                                                   "resolved": False}]})
        code, out = self.cli("resolve", 1, "human", "--body", "閉じます")
        self.assertEqual(code, 1)
        code, out = self.cli("resolve", 1, did, "--body", "None を確かめるようになったことを確認しました")
        self.assertEqual(code, 0, out)
        self.assertEqual([w[0] for w in self.gl.writes[-2:]], ["reply", "resolve"])
        n = len(self.gl.writes)
        code, out = self.cli("resolve", 1, did, "--body", "もう一度")
        self.assertIn("既に解決しています", out)
        self.assertEqual(len(self.gl.writes), n)

    def test_summary_once_and_context_stops(self):
        code, out = self.cli("context", 1)
        self.assertIn("## MR 全体の変更", out)
        self.assertIn("+    y = x * 2", out)
        self.assertEqual(self.cli("summary", 1, "--body", "指摘 0 件")[0], 0)
        self.assertIn("既にあります", self.cli("summary", 1, "--body", "指摘 0 件")[1])
        self.assertEqual(len(self.gl.notes["1"]), 1)
        code, out = self.cli("context", 1)
        self.assertIn("レビューし終えています。何もせずに終えてください", out)

    def test_context_since_shows_new_commits_and_open_threads(self):
        self.cli("comment", 1, "--path", "app.py", "--line", 2, "--body", "x が None のとき落ちます")
        self.gl.reply(1, self.gl.discussions["1"][0]["id"], "alice", "直しました")
        self.gl.push(1, "c" * 40, since_diff=FIX)
        code, out = self.cli("context", 1, "--since", "a" * 40)
        self.assertIn("前回までの指摘（未解決 1 件・解決済み 0 件）", out)
        self.assertIn("app.py:2", out)
        self.assertIn("> 返信（alice）: 直しました", out)
        self.assertIn("前回レビューしたコミット（aaaaaaaa）からの変更", out)
        self.assertIn("+    y = (x or 0) * 2", out)
        code, out = self.cli("context", 1, "--since", "f" * 40)      # force push で前のコミットが無い
        self.assertIn("MR 全体の差分を出します", out)

    def test_show_file(self):
        self.gl.files[("app.py", "a" * 40)] = "def f(x):\n    y = x * 2\n    return y\n"
        code, out = self.cli("show", 1, "app.py", "--from", 2, "--to", 3)
        self.assertIn("     2      y = x * 2", out)
        self.assertNotIn("def f", out)

    def test_show_out_of_range(self):
        self.gl.files[("app.py", "a" * 40)] = "a\nb\n"
        code, out = self.cli("show", 1, "app.py", "--from", 9)
        self.assertIn("全 2 行です", out)
        code, out = self.cli("show", 1, "app.py", "--from", 2, "--to", 1)
        self.assertEqual(code, 2)
        self.assertIn("より前です", out)

    def test_needs_env(self):
        self.env = {"GITLAB_URL": "", "GITLAB_TOKEN": "", "GITLAB_PROJECT": ""}
        with redirect_stderr(io.StringIO()) as err:
            self.assertEqual(self.cli("context", 1)[0], 2)
        self.assertIn("GITLAB_URL", err.getvalue())


# ---------------------------------------------------------------------------
# 作業フォルダ（ローカルのリポジトリ）。GitLab の git は手元の bare リポジトリで代える
# ---------------------------------------------------------------------------

def git(cwd, *args) -> str:
    r = subprocess.run(["git", "-c", "user.name=t", "-c", "user.email=t@example.com", "-c", "init.defaultBranch=main",
                        *args], cwd=cwd, capture_output=True, text=True, encoding="utf-8", errors="replace")
    if r.returncode:
        raise AssertionError(r.stderr)
    return r.stdout.strip()


@unittest.skipUnless(shutil.which("git"), "git が無い")
class LocalRepoTest(_GitLabBase):
    def setUp(self):
        super().setUp()
        self.tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.origin = self.tmp / "origin.git"
        git(self.tmp, "init", "--bare", "-q", str(self.origin))
        self.src = self.tmp / "src"
        git(self.tmp, "init", "-q", str(self.src))
        (self.src / "app.py").write_text("def f(x):\n    return x\n\n", encoding="utf-8")
        (self.src / "other.py").write_text("a = 1\n", encoding="utf-8")
        git(self.src, "add", ".")
        git(self.src, "commit", "-qm", "base")
        self.base = git(self.src, "rev-parse", "HEAD")
        git(self.src, "push", "-q", str(self.origin), "HEAD:refs/heads/main")
        self.head = self.commit("def f(x):\n    y = x * 2\n    return y\n\n", "mr")
        self.gl.put(make_mr(1, sha=self.head, diff_refs={"base_sha": self.base, "start_sha": self.base,
                                                          "head_sha": self.head}))
        self.work = self.tmp / "work"                                    # リポジトリ（無ければ取ってくる）
        self.review = self.tmp / "review"                                # レビュー用の作業ツリー
        self.env.update(MR_REVIEW_REPO_DIR=str(self.work), MR_REVIEW_WORKTREE=str(self.review),
                        MR_REVIEW_GIT_URL=str(self.origin))

    def commit(self, text, msg, ref="refs/merge-requests/1/head") -> str:
        (self.src / "app.py").write_text(text, encoding="utf-8")
        git(self.src, "commit", "-qam", msg)
        sha = git(self.src, "rev-parse", "HEAD")
        git(self.src, "push", "-qf", str(self.origin), f"HEAD:{ref}")
        return sha

    def api(self) -> list[str]:
        out, self.gl.requests[:] = list(self.gl.requests), []
        return out

    def test_context_uses_local_repo_and_checks_out_head(self):
        code, out = self.cli("context", 1)
        self.assertEqual(code, 0, out)
        self.assertIn("diff --git a/app.py b/app.py", out)              # 差分は git で
        self.assertIn("+    y = x * 2", out)
        self.assertIn(f"作業フォルダ（{self.review}）を今のコミットにしてあります", out)
        self.assertEqual((self.review / "app.py").read_text(encoding="utf-8"), "def f(x):\n    y = x * 2\n    return y\n\n")
        self.assertEqual(git(self.review, "rev-parse", "HEAD"), self.head)
        reqs = self.api()
        self.assertFalse([r for r in reqs if "/repository/" in r], reqs)  # compare・ファイルは API に問い合わせない
        self.assertNotIn(TOKEN, (self.work / ".git" / "config").read_text(encoding="utf-8"))
        self.assertEqual(git(self.work, "for-each-ref", "--format=%(refname)", "refs/mr-review"),
                         f"refs/mr-review/heads/main\nrefs/mr-review/mr/1")

    def test_show_comment_summary_reuse_local_and_cache(self):
        self.cli("context", 1)
        self.api()
        code, out = self.cli("show", 1, "app.py", "--from", 2, "--to", 2)
        self.assertIn("     2      y = x * 2", out)
        self.assertEqual(self.api(), [])                                # show は API に問い合わせない
        self.cli("comment", 1, "--path", "app.py", "--line", 2, "--body", "None で落ちます")
        self.cli("summary", 1, "--body", "指摘 1 件")
        reqs = self.api()
        self.assertFalse([r for r in reqs if r.endswith("/diffs")], reqs)   # context で取った差分を使い回す
        self.assertEqual(self.gl.writes[0][2]["position"]["new_line"], 2)

    def test_fetches_only_when_head_is_missing(self):
        self.cli("context", 1)
        moved = self.tmp / "away.git"
        self.origin.rename(moved)                                        # リモートに届かなくても
        code, out = self.cli("context", 1)                               # 同じコミットなら fetch しない
        self.assertIn("diff --git a/app.py", out)
        moved.rename(self.origin)
        new = self.commit("def f(x):\n    y = (x or 0) * 2\n    return y\n\n", "fix")   # コミットが積まれた
        self.gl.push(1, new)
        self.gl.mrs["1"]["diff_refs"] = {"base_sha": self.base, "start_sha": self.base, "head_sha": new}
        code, out = self.cli("context", 1, "--since", self.head)
        self.assertIn(f"前回レビューしたコミット（{self.head[:8]}）からの変更", out)
        self.assertIn("-    y = x * 2\n+    y = (x or 0) * 2", out)
        self.assertNotIn("other.py", out)
        self.assertEqual(git(self.review, "rev-parse", "HEAD"), new)

    def test_since_survives_force_push(self):
        self.cli("context", 1)
        git(self.src, "reset", "-q", "--hard", self.base)
        new = self.commit("def f(x):\n    return x * 2\n\n", "rewritten")      # force push で前のコミットが消えた
        self.gl.push(1, new)
        self.gl.mrs["1"]["diff_refs"] = {"base_sha": self.base, "start_sha": self.base, "head_sha": new}
        code, out = self.cli("context", 1, "--since", self.head)
        self.assertIn("前回レビューしたコミット", out)                   # 前のコミットは手元に残っている
        self.assertIn("+    return x * 2", out)

    def test_falls_back_to_api_when_git_fails(self):
        self.env["MR_REVIEW_GIT_URL"] = str(self.tmp / "missing.git")
        err = io.StringIO()
        with redirect_stderr(err):
            code, out = self.cli("context", 1)
        self.assertEqual(code, 0, out)
        self.assertIn("## MR 全体の変更", out)
        self.assertIn("作業フォルダは今のコミットになっていません", out)
        self.assertIn("差分は API から取ります", err.getvalue())

    def test_prepare_clones_once(self):
        del self.env["MR_REVIEW_GIT_URL"]
        self.gl.project["http_url_to_repo"] = str(self.origin)          # URL は API の http_url_to_repo から
        code, out = self.cli("prepare")
        self.assertEqual(code, 0, out)
        self.assertIn("取ってきました", out)
        self.assertEqual((self.review / "app.py").read_text(encoding="utf-8"), "def f(x):\n    return x\n\n")
        code, out = self.cli("prepare")
        self.assertIn("既にあるものを使います", out)

    def test_existing_working_copy_is_left_alone(self):
        # 自分の作業コピー（ブランチで作業中・書きかけの変更あり）をそのまま MR_REVIEW_REPO_DIR にする
        mine = self.tmp / "mine"
        git(self.tmp, "clone", "-q", str(self.origin), str(mine))
        git(mine, "checkout", "-qb", "my-work")
        (mine / "other.py").write_text("a = 2  # 書きかけ\n", encoding="utf-8")
        (mine / "notes.txt").write_text("メモ\n", encoding="utf-8")
        del self.env["MR_REVIEW_GIT_URL"]                               # リモート origin から取る
        self.env["MR_REVIEW_REPO_DIR"] = str(mine)
        refs = git(mine, "for-each-ref", "--format=%(refname) %(objectname)", "refs/heads", "refs/remotes")
        code, out = self.cli("context", 1)
        self.assertEqual(code, 0, out)
        self.assertIn("+    y = x * 2", out)
        self.assertEqual(git(mine, "rev-parse", "--abbrev-ref", "HEAD"), "my-work")           # ブランチはそのまま
        self.assertEqual((mine / "other.py").read_text(encoding="utf-8"), "a = 2  # 書きかけ\n")  # 書きかけも
        self.assertTrue((mine / "notes.txt").exists())
        self.assertEqual(git(mine, "for-each-ref", "--format=%(refname) %(objectname)", "refs/heads", "refs/remotes"),
                         refs)                                           # ブランチ・追跡ブランチを増やさず、動かさない
        self.assertEqual(git(self.review, "rev-parse", "HEAD"), self.head)                   # MR は別の作業ツリーに
        self.assertFalse((self.tmp / "work").exists())                  # 取り直していない

    def test_refuses_to_overwrite_other_folders(self):
        mine = self.tmp / "mine"
        git(self.tmp, "clone", "-q", str(self.origin), str(mine))
        (mine / "other.py").write_text("書きかけ\n", encoding="utf-8")
        del self.env["MR_REVIEW_GIT_URL"]
        self.env.update(MR_REVIEW_REPO_DIR=str(mine), MR_REVIEW_WORKTREE=str(mine))   # 作業ツリーをリポジトリそのものに
        err = io.StringIO()
        with redirect_stderr(err):
            code, out = self.cli("context", 1)
        self.assertIn("MR_REVIEW_REPO_DIR と同じにはできません", err.getvalue())
        self.assertEqual((mine / "other.py").read_text(encoding="utf-8"), "書きかけ\n")
        stuff = self.tmp / "stuff"
        stuff.mkdir()
        (stuff / "keep.txt").write_text("消さない\n", encoding="utf-8")
        self.env["MR_REVIEW_WORKTREE"] = str(stuff)                     # 関係の無いフォルダ
        with redirect_stderr(err):
            self.cli("context", 1)
        self.assertIn("空ではなく、このリポジトリの作業ツリーでもありません", err.getvalue())
        self.assertTrue((stuff / "keep.txt").exists())

    def test_token_goes_only_to_the_gitlab_host(self):
        def header(url):
            env = gm.LocalRepo(self.work, TOKEN, url=url, gitlab_url="https://GitLab.example")._env()
            n = int(env.get("GIT_CONFIG_COUNT") or 0) - 1
            return env.get(f"GIT_CONFIG_VALUE_{n}", "") if env.get(f"GIT_CONFIG_KEY_{n}") == "http.extraHeader" else ""
        self.assertIn("Basic ", header("https://gitlab.example/g/p.git"))
        self.assertEqual(header("https://github.com/g/p.git"), "")      # ほかのホストには渡さない
        self.assertEqual(header("git@gitlab.example:g/p.git"), "")      # SSH はその PC の鍵で


# ---------------------------------------------------------------------------
# 設定の例をそのまま本物の change-watch で回す
# ---------------------------------------------------------------------------

@unittest.skipUnless(CHANGE_WATCH.is_file() and yaml is not None, "change-watch か PyYAML が無い")
class FlowTest(_GitLabBase):
    def setUp(self):
        super().setUp()
        self.tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.tmp, True)
        cfg = yaml.safe_load((ROOT / "examples" / "rules" / "change-watch.yaml.example").read_text(encoding="utf-8"))
        cfg["state_dir"] = str(self.tmp / "state")
        src = cfg["sources"]["gitlab-mrs"]
        src.pop("schedule")
        src.update(jitter_sec=0, failure_backoff_sec=0, retry={"attempts": 1})
        tgt = cfg["rules"]["mr-review"]["targets"][0]
        tgt.update(input_template=str(ROOT / "examples" / "rules" / "prompt.md.example"), retry_backoff_sec=0)
        self.cfg = self.tmp / "change-watch.yaml"
        self.cfg.write_text(yaml.safe_dump(cfg, allow_unicode=True), encoding="utf-8")
        self.conn = self.tmp / "connections.json"
        self.conn.write_text(json.dumps({"gitlab": [{"label": "default", "url": self.gl.url, "token": TOKEN}]}),
                             encoding="utf-8")
        self.env["MR_REVIEW_CLI"] = "python3 gitlab_mr.py"

    def aw(self, *args) -> str:
        r = subprocess.run([sys.executable, str(CHANGE_WATCH), "--plugin", str(PLUGIN), "--config", str(self.cfg),
                            "--connections", str(self.conn), *args], env={**os.environ, **self.env},
                           capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=120)
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        return r.stdout

    def cycle(self, agent=None, exit_code="0"):
        """1 回の起動。(受け渡しの ID, プロンプト)。レビューするものが無ければ (None, None)。"""
        self.aw("fetch", "--force", "--no-jitter")
        self.aw("evaluate", "--rule", "mr-review")
        delivery = self.aw("claim", "--rule", "mr-review").strip()
        if not delivery:
            return None, None
        prompt = self.aw("render", delivery)
        if agent:
            agent(prompt)
        self.aw("finish", delivery, "--exit-code", exit_code)
        return delivery, prompt

    def review(self, iid, findings=(), resolve=()):
        """kiro-cli の代わり: context を読み、指摘し、直ったものを閉じ、まとめを書く。"""
        def run(prompt):
            since = prompt.split("--since=", 1)[1].split("`", 1)[0]
            _, ctx = self.cli("context", iid, f"--since={since}")
            self.contexts.append(ctx)
            if "何もせずに終えてください" in ctx:
                return
            for path, line, body in findings:
                self.cli("comment", iid, "--path", path, "--line", line, "--body", body)
            for did in resolve:
                self.cli("resolve", iid, did, "--body", "直ったことを確かめました")
            self.cli("summary", iid, "--body", f"新しい指摘 {len(findings)} 件・閉じた指摘 {len(resolve)} 件")
        self.contexts: list[str] = getattr(self, "contexts", [])
        return run

    def test_review_once_then_close_after_fix(self):
        self.gl.put(make_mr(1))
        d, prompt = self.cycle(self.review(1, findings=[("app.py", 2, "x が None のとき落ちます")]))
        self.assertIsNotNone(d)                                          # 初回は開いている MR もレビューする
        self.assertIn("!1「MR 1」をレビューしてください（初めてのレビュー、1 回目）", prompt)
        self.assertIn("`python3 gitlab_mr.py context 1 --since=`", prompt)
        self.assertEqual(len(self.gl.discussions["1"]), 1)
        # 指摘と人の返信（コメント）だけでは、もう一度レビューしない
        self.gl.reply(1, self.gl.discussions["1"][0]["id"], "alice", "直します")
        self.gl.mrs["1"]["user_notes_count"] = 3
        self.assertEqual(self.cycle(), (None, None))
        # 直したコミットを積むと、前回のコミットからの差分でもう一度レビューし、指摘を閉じる
        self.gl.push(1, "c" * 40, since_diff=FIX)
        did = self.gl.discussions["1"][0]["id"]
        d, prompt = self.cycle(self.review(1, resolve=[did]))
        self.assertIn("再レビュー（前回は aaaaaaaa）", prompt)
        self.assertIn(f"--since={'a' * 40}", prompt)
        self.assertIn("前回までの指摘（未解決 1 件", self.contexts[-1])
        self.assertIn("+    y = (x or 0) * 2", self.contexts[-1])
        self.assertTrue(all(n["resolved"] for n in self.gl.discussions["1"][0]["notes"]))
        self.assertEqual(self.cycle(), (None, None))
        self.assertEqual(len(self.gl.notes["1"]), 2)                     # まとめはコミットごとに 1 回

    def test_push_during_review_is_reviewed_next(self):
        self.gl.put(make_mr(1))

        def agent(prompt):
            self.review(1)(prompt)
            self.gl.push(1, "c" * 40, since_diff=FIX)                    # レビュー中に人がコミットを積んだ
        self.cycle(agent)
        d, prompt = self.cycle(self.review(1))
        self.assertIsNotNone(d)
        self.assertIn(f"--since={'a' * 40}", prompt)
        self.assertEqual(self.cycle(), (None, None))

    def test_rebase_without_changes_does_not_start_kiro(self):
        self.gl.put(make_mr(1))
        self.cycle(self.review(1, findings=[("app.py", 2, "x が None のとき落ちます")]))
        self.gl.push(1, "c" * 40, diff=DIFF_A, since_diff="",
                     extra=[{"old_path": "main_only.py", "new_path": "main_only.py", "diff": "@@ -1 +1 @@\n-a\n+b\n"}])
        self.assertEqual(self.cycle(), (None, None))                    # head は変わったが、中身は同じ
        self.gl.push(1, "d" * 40, diff=DIFF_A, since_diff="")           # もう一度リベースしても同じ
        self.assertEqual(self.cycle(), (None, None))
        self.gl.push(1, "e" * 40, since_diff=FIX)                        # 中身を直すと、レビューする
        d, prompt = self.cycle(self.review(1, resolve=[self.gl.discussions["1"][0]["id"]]))
        self.assertIsNotNone(d)
        self.assertNotIn("main_only.py", self.contexts[-1])
        self.assertEqual(len(self.gl.notes["1"]), 2)

    def test_reopened_mr_is_not_reviewed_again(self):
        # 一覧から消えた MR の観測値と処理し終えた状態は removed_days（既定 30 日）で消える。それを過ぎて開き直した MR
        cfg = yaml.safe_load(self.cfg.read_text(encoding="utf-8"))
        cfg["retention"] = {"removed_days": 0}
        self.cfg.write_text(yaml.safe_dump(cfg, allow_unicode=True), encoding="utf-8")
        self.gl.put(make_mr(1))
        self.cycle(self.review(1))
        self.gl.mrs["1"]["state"] = "closed"
        self.cycle()                                                     # 一覧から消える
        self.aw("gc")
        self.gl.mrs["1"]["state"] = "opened"
        self.assertEqual(self.cycle(), (None, None))                    # まとめが残っているので kiro-cli は起きない
        self.assertEqual(len(self.gl.notes["1"]), 1)

    def test_target_branch_change_is_reviewed(self):
        self.gl.put(make_mr(1))
        self.cycle(self.review(1))
        self.gl.mrs["1"]["target_branch"] = "release"
        self.gl.diffs["1"] = [{"old_path": "app.py", "new_path": "app.py", "diff": DIFF_B}]
        d, _ = self.cycle(self.review(1))
        self.assertIsNotNone(d)
        self.assertEqual(self.cycle(), (None, None))

    def test_draft_waits_until_ready(self):
        self.gl.put(make_mr(2, draft=True))
        self.assertEqual(self.cycle(), (None, None))
        self.gl.mrs["2"]["draft"] = False
        d, prompt = self.cycle(self.review(2))
        self.assertIn("!2", prompt)
        self.assertEqual(self.cycle(), (None, None))

    def test_failure_retried_without_duplicate_findings(self):
        self.gl.put(make_mr(1))

        def crash(prompt):                                               # 指摘を書いたところで kiro-cli が落ちた
            self.cli("comment", 1, "--path", "app.py", "--line", 2, "--body", "x が None のとき落ちます")
        d, _ = self.cycle(crash, exit_code="1")
        d2, prompt = self.cycle(self.review(1, findings=[("app.py", 2, "x が None のとき落ちます")]))
        self.assertEqual(d2, d)
        self.assertIn("2 回目", prompt)
        self.assertEqual(len(self.gl.discussions["1"]), 1)
        self.assertEqual(self.cycle(), (None, None))

    def test_example_dag_reviews_once(self):
        """例の Dagu のワークフローを Dagu と同じ順で本物の change-watch に流す（kiro-cli の代わりにテストがレビューする）。"""
        sys.path.insert(0, str(CHANGE_WATCH.parent / "test"))
        import dagrun
        dag = ROOT / "examples" / "rules" / "change-watch-mr-review.yaml.example"
        dagrun.check_shape(self, dagrun.load(dag), dag.name)
        self.gl.put(make_mr(1))
        prompts: list[str] = []

        def handle(step, vars):
            self.assertEqual((step["action"], step["with"]["provider"], step["with"]["prompt"]),
                             ("harness.run", "kiro", "${INPUT}"))
            self.assertEqual(step["timeout_sec"], 1800)                   # change-watch の設定の timeout_sec と同じ
            prompts.append(vars["INPUT"])
            self.review(1, findings=[("app.py", 2, "x が None のとき落ちます")])(vars["INPUT"])
            return 0

        def run():
            return dagrun.run(dag, aw=[sys.executable, str(CHANGE_WATCH)], head=["--config", str(self.cfg)],
                              paths={"/home/you/.local/share/change-watch/agents/gitlab-mr-review-agent/gitlab_mr.py": str(PLUGIN),
                                     "/home/you/.github/connections.yaml": str(self.conn),
                                     "/home/you/.config/change-watch/gitlab-mr-review-agent/change-watch.yaml": str(self.cfg)},
                              handle=handle, env=self.env)
        r = run()
        self.assertEqual(r, {"fetch": 0, "evaluate": 0, "claim": 0, "render": 0, "handle": 0, "finish": 0})
        self.assertIn("!1「MR 1」をレビューしてください", prompts[0])
        self.assertEqual(len(self.gl.discussions["1"]), 1)
        r = run()                                                        # 同じコミットはもう流さない
        self.assertEqual((r["handle"], r["finish"], len(prompts)), (None, None, 1))
        gc = ROOT / "change-watch-gitlab-mr-review-agent-maintenance.yaml.example"
        dagrun.check_shape(self, dagrun.load(gc), gc.name)
        self.assertEqual(dagrun.run(gc, aw=[sys.executable, str(CHANGE_WATCH)], head=[], env=self.env, paths={
            "/home/you/.github/connections.yaml": str(self.conn),
            "/home/you/.config/change-watch/gitlab-mr-review-agent/change-watch.yaml": str(self.cfg)}), {"gc": 0})

    def test_default_example(self):
        """既定の例: 条件は Dagu の preconditions が判定し、change-watch は get / commit だけを受け持つ形。"""
        sys.path.insert(0, str(CHANGE_WATCH.parent / "test"))
        import dagrun
        d = ROOT
        cfg = yaml.safe_load((d / "change-watch.yaml.example").read_text(encoding="utf-8"))
        self.assertNotIn("rules", cfg)                                   # 条件は change-watch に書かない
        cfg["state_dir"] = str(self.tmp / "state-dagu")
        src = cfg["sources"]["gitlab-mrs"]
        for k in ("schedule", "jitter_sec", "busy_wait_sec"):
            src.pop(k)
        src.update(failure_backoff_sec=0, retry={"attempts": 1})
        conf = self.tmp / "change-watch-dagu.yaml"
        conf.write_text(yaml.safe_dump(cfg, allow_unicode=True), encoding="utf-8")
        dag = d / "change-watch-mr-review.yaml.example"
        dagrun.check_shape(self, dagrun.load(dag), dag.name)
        prompts: list[str] = []

        def handle(step, vars):
            self.assertEqual((step["action"], step["with"]["provider"]), ("harness.run", "kiro"))
            prompt = dagrun._expand(step["with"]["prompt"], vars)
            prompts.append(prompt)
            self.review(1, findings=[("app.py", 2, "x が None のとき落ちます")])(prompt)
            return 0

        def run():
            return dagrun.run(dag, aw=[sys.executable, str(CHANGE_WATCH)], head=[], handle=handle,
                              env={**os.environ, **self.env},
                              paths={"/home/you/.github/connections.yaml": str(self.conn),
                                     "/home/you/.config/change-watch/gitlab-mr-review-agent/change-watch.yaml": str(conf)})
        self.gl.put(make_mr(1))
        self.gl.put(make_mr(2, draft=True))
        self.assertEqual(run(), {"get": 0, "review": 0, "commit": 0})    # 初回も開いている MR をレビューする
        self.assertIn("!1「MR 1」をレビューしてください", prompts[0])
        self.assertIn("`python3 gitlab_mr.py context 1 --since=`", prompts[0])
        self.assertEqual(len(self.gl.discussions["1"]), 1)
        self.assertEqual(run(), {"get": 0, "review": None, "commit": 0})  # ドラフトは条件に合わない（commit はする）
        self.assertEqual(run(), {"get": 0, "review": None, "commit": None})   # 同じコミットはもう流さない
        self.gl.mrs["1"]["user_notes_count"] = 3                          # コメントだけでは起きない
        self.assertEqual(run()["review"], None)
        self.gl.push(1, "b" * 40)
        run()
        self.assertEqual(len(prompts), 2)
        self.assertIn("--since=" + "a" * 40 + "`", prompts[1])           # 前回レビューしたコミットからの差分

    def test_merged_while_waiting_is_skipped(self):
        self.gl.put(make_mr(1))
        self.aw("fetch", "--force", "--no-jitter")
        self.aw("evaluate", "--rule", "mr-review")
        self.gl.mrs["1"]["state"] = "merged"
        self.assertEqual(self.aw("claim", "--rule", "mr-review").strip(), "")


if __name__ == "__main__":
    unittest.main()
