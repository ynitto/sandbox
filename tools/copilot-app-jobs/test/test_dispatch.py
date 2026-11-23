"""振り先（--to）のある依頼: 自動実行がそのフォルダのセッションに振って待たずに進み、同じ振り先へは 1 つずつ振る。"""

from __future__ import annotations

import contextlib
import io
import os
import sys
import tempfile
import time
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import copilot_app_job as job  # noqa: E402


def call(*argv: str) -> "tuple[int, str, str]":
    out, err = io.StringIO(), io.StringIO()
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
        code = job.main(list(argv))
    return code, out.getvalue(), err.getvalue()


class DispatchTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        root = Path(self.tmp.name)
        self.hub = root / "hub"            # 自動実行が動くフォルダ（受け箱がある）
        self.a = root / "sandbox"
        self.b = root / "other"
        for folder in (self.hub, self.a, self.b):
            folder.mkdir()
        self.box = self.hub / job.BOX_NAME
        for folder in (self.a, self.b):
            self.assertEqual(call("target", "add", "--dir", str(self.hub), "--to", str(folder))[0], 0)

    def tearDown(self):
        self.tmp.cleanup()

    def submit(self, to: Path, prompt: str) -> str:
        code, out, _ = call("run", "--dir", str(self.hub), "--to", str(to), "--prompt", prompt, "--no-wait")
        self.assertEqual(code, 0)
        return out.strip()

    def next(self) -> str:
        return call("next", "--dir", str(self.hub))[1]

    def handed(self, job_id: str, *extra: str) -> str:
        code, out, _ = call("dispatched", "--dir", str(self.hub), "--id", job_id, *extra)
        self.assertEqual(code, 0)
        return out

    def finish(self, job_id: str, status: str = "ok", note: str = "https://gitlab.example.com/g/p/-/merge_requests/1") -> int:
        return call("finish", "--dir", str(self.hub), "--id", job_id, "--status", status, "--note", note)[0]

    def result(self, job_id: str) -> str:
        return (self.box / "results" / f"{job_id}.md").read_text(encoding="utf-8")

    def test_targets_must_be_registered(self):
        code, _, err = call("run", "--dir", str(self.hub), "--to", str(self.hub), "--prompt", "x", "--no-wait")
        self.assertEqual(code, job.EXIT_USAGE)
        self.assertIn("登録されていません", err)
        code, _, err = call("run", "--dir", str(self.hub), "--to", str(self.hub / "nothing"), "--prompt", "x")
        self.assertEqual(code, job.EXIT_USAGE)
        listed = call("target", "list", "--dir", str(self.hub))[1].splitlines()
        self.assertEqual(listed, [str(self.a.resolve()), str(self.b.resolve())])
        call("target", "remove", "--dir", str(self.hub), "--to", str(self.b))
        self.assertEqual(call("target", "list", "--dir", str(self.hub))[1].splitlines(), [str(self.a.resolve())])
        call("target", "add", "--dir", str(self.hub), "--to", str(self.a))     # 二度足しても 1 行
        self.assertEqual(len(call("target", "list", "--dir", str(self.hub))[1].splitlines()), 1)

    def test_dispatch_does_not_wait_and_queues_per_folder(self):
        a1 = self.submit(self.a, "README の誤字を直して MR を出して")
        a2 = self.submit(self.a, "テストを足して MR を出して")
        b1 = self.submit(self.b, "issue #3 に調査結果を書いて")

        text = self.next()
        self.assertIn(f"依頼 {a1} は、このセッションでは実行しません", text)
        self.assertIn(f"ローカルの {self.a.name} フォルダ", text)
        self.assertIn("README の誤字を直して MR を出して", text)
        self.assertIn(f"finish --dir \"{self.hub}\" --id {a1} --status ok", text)
        self.assertIn(f"dispatched --dir \"{self.hub}\" --id {a1}", text)
        self.assertNotIn("<!-- copilot-app-job", text)

        # 渡せたら、待たずに次へ。同じ振り先の a2 は飛ばして b1
        text = self.handed(a1)
        self.assertIn(f"依頼 {b1}", text)
        self.assertTrue((self.box / "dispatched" / f"{a1}.md").exists())
        self.assertEqual(self.handed(b1), "なし\n")     # a2 は a1 が終わるまで待つ
        self.assertTrue((self.box / "pending" / f"{a2}.md").exists())

        # 振られたセッションが終わりを知らせると、a の次が出る
        self.assertEqual(self.finish(a1), 0)
        self.assertIn("merge_requests/1", self.result(a1))
        self.assertTrue(self.result(a1).startswith("status: ok"))
        self.assertIn(f"依頼 {a2}", self.next())
        self.assertEqual(self.finish(a1), 0)            # 二度目は「既に書かれています」

    def test_failed_launch_frees_folder(self):
        a1 = self.submit(self.a, "一つ目")
        a2 = self.submit(self.a, "二つ目")
        self.next()
        text = self.handed(a1, "--failed", "フォルダを開けなかった")
        self.assertIn(f"依頼 {a2}", text)
        self.assertIn("起こせませんでした: フォルダを開けなかった", self.result(a1))
        self.assertTrue(self.result(a1).startswith("status: failed"))

    def test_stale_dispatch_fails_and_frees_folder(self):
        a1 = self.submit(self.a, "一つ目")
        a2 = self.submit(self.a, "二つ目")
        self.next()
        self.handed(a1)
        old = time.time() - job.DISPATCH_STALE - 10
        os.utime(self.box / "dispatched" / f"{a1}.md", (old, old))
        self.assertIn(f"依頼 {a2}", self.next())
        self.assertIn("終わりの知らせが来ないまま", self.result(a1))
        self.assertEqual(self.finish(a1), 0)            # 遅れて来た知らせは結果を変えない
        self.assertTrue(self.result(a1).startswith("status: failed"))

    def test_dispatch_stale_option_is_carried(self):
        self.submit(self.a, "x")
        text = call("next", "--dir", str(self.hub), "--dispatch-stale", "600")[1]
        self.assertIn("--dispatch-stale 600", text)

    def test_unregistered_after_submit_is_rejected(self):
        a1 = self.submit(self.a, "一つ目")
        b1 = self.submit(self.b, "二つ目")
        call("target", "remove", "--dir", str(self.hub), "--to", str(self.a))
        self.assertIn(f"依頼 {b1}", self.next())
        self.assertIn("登録されていません", self.result(a1))

    def test_plain_jobs_still_run_here(self):
        plain = self.submit_plain("このフォルダで作業して")
        a1 = self.submit(self.a, "振る")
        text = self.next()
        self.assertIn(f"依頼 {plain} です。下の「依頼」を、このフォルダを作業場所として実行します", text)
        self.assertNotIn(a1, text)

    def submit_plain(self, prompt: str) -> str:
        return call("run", "--dir", str(self.hub), "--prompt", prompt, "--no-wait")[1].strip()

    def test_status_shows_targets(self):
        a1 = self.submit(self.a, "x")
        self.next()
        self.handed(a1)
        out = call("status", "--dir", str(self.hub))[1]
        self.assertIn("dispatched: 1", out)
        self.assertIn(f"{a1}  → {self.a.resolve()}", out)

    def test_meta_parse(self):
        meta, body = job.split_meta('<!-- copilot-app-job: {"to": "C:/x"} -->\n本文\n')
        self.assertEqual((meta, body), ({"to": "C:/x"}, "本文\n"))
        self.assertEqual(job.split_meta("<!-- copilot-app-job: {broken} -->\n本文")[0], {})
        self.assertEqual(job.split_meta("本文 <!-- copilot-app-job: {} -->")[0], {})

    def test_norm_compares_windows_paths_loosely(self):
        self.assertEqual(job.norm("C:\\Work\\Sandbox\\"), job.norm("c:/work/sandbox"))


if __name__ == "__main__":
    unittest.main()
