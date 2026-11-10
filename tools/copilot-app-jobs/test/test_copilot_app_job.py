"""copilot_app_job の受け箱のやりとりを、アプリ役をテストが演じて確かめる。"""

from __future__ import annotations

import contextlib
import io
import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import copilot_app_job as job  # noqa: E402


class FakeClock:
    """sleep で進む時計。sleep のたびに on_sleep を呼び、アプリ役の動きを差し込む。"""

    def __init__(self, on_sleep=None):
        self.now = 0.0
        self.on_sleep = on_sleep

    def clock(self) -> float:
        return self.now

    def sleep(self, sec: float) -> None:
        self.now += sec
        if self.on_sleep:
            self.on_sleep(self.now)


class BoxTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.work = Path(self.tmp.name)
        self.box = job.box_of(self.work)

    def claim(self, job_id: str) -> None:
        (self.box / "pending" / f"{job_id}.md").rename(self.box / "running" / f"{job_id}.md")

    def write_result(self, job_id: str, text: str) -> None:
        (self.box / "results" / f"{job_id}.md").write_text(text, encoding="utf-8")

    def test_submit_puts_prompt_in_pending_only_when_complete(self):
        job_id = job.submit(self.box, "README を直して", "nightly fix")
        self.assertTrue(job_id.endswith("-nightly-fix"))
        self.assertEqual((self.box / "pending" / f"{job_id}.md").read_text(encoding="utf-8"), "README を直して\n")
        self.assertEqual(list((self.box / "tmp").iterdir()), [])

    def test_job_ids_sort_in_submit_order(self):
        first = job.new_job_id()
        time.sleep(1.1)
        self.assertLess(first, job.new_job_id())

    def test_parse_result(self):
        self.assertEqual(job.parse_result("status: ok\n\n直しました\n"), ("ok", "直しました"))
        self.assertEqual(job.parse_result("﻿\n**Status: FAILED**\n\n理由"), ("failed", "理由"))
        self.assertIsNone(job.parse_result(""))
        self.assertIsNone(job.parse_result("作業中です\nstatus: ok"))

    def test_wait_returns_ok_after_result_settles(self):
        job_id = job.submit(self.box, "やって")

        def app(now):
            if now == 2:
                self.claim(job_id)
            if now == 4:
                self.write_result(job_id, "status: ok\n\nできた\n")

        fake = FakeClock(app)
        code, body = job.wait_result(self.box, job_id, 60, poll=1, settle=3, clock=fake.clock, sleep=fake.sleep)
        self.assertEqual((code, body), (job.EXIT_OK, "できた"))
        self.assertGreaterEqual(fake.now, 7)  # 書かれてから settle 秒は待つ

    def test_wait_does_not_read_a_result_still_being_written(self):
        job_id = job.submit(self.box, "やって")
        self.claim(job_id)

        def app(now):
            if now == 1:
                self.write_result(job_id, "status: ok\n\n途中")
            if now == 3:
                self.write_result(job_id, "status: failed\n\nやっぱり無理でした\n")

        fake = FakeClock(app)
        code, body = job.wait_result(self.box, job_id, 60, poll=1, settle=3, clock=fake.clock, sleep=fake.sleep)
        self.assertEqual((code, body), (job.EXIT_FAILED, "やっぱり無理でした"))

    def test_timeout_withdraws_a_job_nobody_picked_up(self):
        job_id = job.submit(self.box, "やって")
        fake = FakeClock()
        code, _ = job.wait_result(self.box, job_id, 5, poll=1, clock=fake.clock, sleep=fake.sleep)
        self.assertEqual(code, job.EXIT_NOT_PICKED)
        self.assertEqual(list((self.box / "pending").iterdir()), [])

    def test_timeout_leaves_a_job_already_started(self):
        job_id = job.submit(self.box, "やって")
        self.claim(job_id)
        fake = FakeClock()
        code, message = job.wait_result(self.box, job_id, 5, poll=1, clock=fake.clock, sleep=fake.sleep)
        self.assertEqual(code, job.EXIT_NOT_FINISHED)
        self.assertIn(job_id, message)
        self.assertTrue((self.box / "running" / f"{job_id}.md").exists())

    def test_wait_pending(self):
        fake = FakeClock()
        self.assertFalse(job.wait_pending(self.box, 3, poll=1, clock=fake.clock, sleep=fake.sleep))
        job.submit(self.box, "やって")
        self.assertTrue(job.wait_pending(self.box, 3, poll=1, clock=fake.clock, sleep=fake.sleep))

    def test_automation_prompt_names_the_box_and_every_state(self):
        text = job.automation_prompt(self.work, self.box)
        for state in job.STATES:
            self.assertIn(f"{job.BOX_NAME}/{state}", text)
        self.assertIn("status: ok", text)
        self.assertNotIn(" wait ", text)
        stay = job.automation_prompt(self.work, self.box, stay_min=50)
        self.assertIn(f'wait --dir "{self.work}"', stay)
        self.assertIn("50 分", stay)


class CliTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.work = Path(self.tmp.name)
        self.box = job.box_of(self.work)

    def run_cli(self, *argv: str) -> "tuple[int, str, str]":
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            code = job.main(list(argv))
        return code, out.getvalue(), err.getvalue()

    def test_run_end_to_end_with_an_app_that_follows_the_prompt(self):
        def app():
            pending = self.box / "pending"
            deadline = time.time() + 10
            while time.time() < deadline:
                files = sorted(pending.glob("*.md")) if pending.is_dir() else []
                if files:
                    running = self.box / "running" / files[0].name
                    files[0].rename(running)
                    (self.box / "results" / files[0].name).write_text(
                        f"status: ok\n\n受け取った依頼: {running.read_text(encoding='utf-8').strip()}\n", encoding="utf-8"
                    )
                    running.rename(self.box / "done" / files[0].name)
                    return
                time.sleep(0.05)

        thread = threading.Thread(target=app)
        thread.start()
        code, out, _ = self.run_cli(
            "run", "--dir", str(self.work), "--prompt", "テストを足して", "--timeout", "10", "--poll", "0.05", "--settle", "0.2"
        )
        thread.join()
        self.assertEqual(code, job.EXIT_OK)
        self.assertEqual(out.strip(), "受け取った依頼: テストを足して")

    def test_run_rejects_empty_prompt_and_missing_dir(self):
        code, _, err = self.run_cli("run", "--dir", str(self.work), "--prompt", "  ")
        self.assertEqual(code, job.EXIT_USAGE)
        self.assertIn("空", err)
        code, _, _ = self.run_cli("run", "--dir", str(self.work / "nope"), "--prompt", "x")
        self.assertEqual(code, job.EXIT_USAGE)

    def test_no_wait_prints_the_job_id(self):
        code, out, _ = self.run_cli("run", "--dir", str(self.work), "--prompt", "x", "--no-wait")
        self.assertEqual(code, job.EXIT_OK)
        self.assertTrue((self.box / "pending" / f"{out.strip()}.md").exists())

    def test_status_counts(self):
        self.run_cli("run", "--dir", str(self.work), "--prompt", "x", "--no-wait")
        code, out, _ = self.run_cli("status", "--dir", str(self.work))
        self.assertEqual(code, job.EXIT_OK)
        self.assertIn("pending: 1", out)
        self.assertIn("results: 0", out)


if __name__ == "__main__":
    unittest.main()
