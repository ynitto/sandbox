"""copilot_app_job の受け箱のやりとりを、アプリ役をテストが演じて確かめる。"""

from __future__ import annotations

import contextlib
import io
import os
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import copilot_app_job as job  # noqa: E402

SCRIPT = Path(job.__file__).resolve()


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

    def test_wait_reads_a_result_written_just_before_the_deadline(self):
        job_id = job.submit(self.box, "やって")
        self.claim(job_id)

        def app(now):
            if now == 4:  # 締め切り（5 秒）の直前に書き終わる
                job.finish(self.box, job_id, "ok", "間に合いました")

        fake = FakeClock(app)
        code, body = job.wait_result(self.box, job_id, 5, poll=1, settle=3, clock=fake.clock, sleep=fake.sleep)
        self.assertEqual((code, body), (job.EXIT_OK, "間に合いました"))

    def test_claim_takes_oldest_first_and_only_once(self):
        first = job.submit(self.box, "1 つ目", "a")
        time.sleep(1.1)
        second = job.submit(self.box, "2 つ目", "b")
        self.assertEqual(job.claim_next(self.box), first)
        self.assertEqual(job.claim_next(self.box), second)
        self.assertIsNone(job.claim_next(self.box))
        self.assertTrue((self.box / "running" / f"{first}.md").exists())

    def test_claim_reoffers_a_job_left_running_by_a_dead_session(self):
        job_id = job.submit(self.box, "やって")
        self.assertEqual(job.claim_next(self.box, wall=lambda: 1000.0), job_id)
        self.assertIsNone(job.claim_next(self.box, stale_sec=600, wall=lambda: 1500.0))
        self.assertEqual(job.claim_next(self.box, stale_sec=600, wall=lambda: 1700.0), job_id)
        # 出し直した直後は、また取りかかり中として扱う
        self.assertIsNone(job.claim_next(self.box, stale_sec=600, wall=lambda: 1800.0))

    def test_wait_claim_waits_in_chunks_until_the_deadline(self):
        job.ensure_box(self.box)
        fake = FakeClock()
        got, more = job.wait_claim(self.box, until=100, chunk=10, poll=1, wall=fake.clock, sleep=fake.sleep)
        self.assertEqual((got, more, fake.now), (None, True, 10))
        fake.now = 95
        got, more = job.wait_claim(self.box, until=100, chunk=10, poll=1, wall=fake.clock, sleep=fake.sleep)
        self.assertEqual((got, more, fake.now), (None, False, 100))

    def test_wait_claim_returns_as_soon_as_a_job_arrives(self):
        job.ensure_box(self.box)
        ids = []
        fake = FakeClock(lambda now: ids.append(job.submit(self.box, "やって")) if now == 3 else None)
        got, _ = job.wait_claim(self.box, until=100, chunk=100, poll=1, wall=fake.clock, sleep=fake.sleep)
        self.assertEqual((got, fake.now), (ids[0], 3))

    def test_finish_writes_a_result_run_can_read(self):
        job_id = job.submit(self.box, "やって")
        self.assertFalse(job.finish(self.box, job_id, "ok", "まだ取っていない"))
        job.claim_next(self.box)
        self.assertTrue(job.finish(self.box, job_id, "failed", "無理でした\n"))
        text = (self.box / "results" / f"{job_id}.md").read_text(encoding="utf-8")
        self.assertEqual(job.parse_result(text), ("failed", "無理でした"))
        self.assertTrue((self.box / "done" / f"{job_id}.md").exists())

    def test_automation_prompt_is_one_command_and_no_procedure(self):
        text = job.automation_prompt(self.work, None)
        self.assertIn(f'next --dir "{self.work}"', text)
        self.assertNotIn("--stay", text)
        self.assertLess(len(text), 400)  # 依頼が無い回に毎回読ませるので、短いままにする
        for word in ("pending", "results", "status:"):
            self.assertNotIn(word, text)
        stay = job.automation_prompt(self.work, None, stay_min=50, chunk=600)
        self.assertIn("--stay 3000 --chunk 600", stay)


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

    def test_next_says_nashi_when_empty(self):
        code, out, _ = self.run_cli("next", "--dir", str(self.work))
        self.assertEqual((code, out), (job.EXIT_OK, "なし\n"))

    def test_next_with_stay_hands_back_the_command_to_continue(self):
        code, out, _ = self.run_cli("next", "--dir", str(self.work), "--stay", "60", "--chunk", "0.1", "--poll", "0.05")
        self.assertEqual(code, job.EXIT_OK)
        self.assertIn("next --dir", out)
        self.assertRegex(out, r"--until \d+ --chunk 0")

    def test_run_end_to_end_with_an_app_that_calls_next_and_done(self):
        seen = []

        def app_cli(*argv: str) -> str:
            # アプリ役は別プロセスで呼ぶ（本物と同じ。同じプロセスだと標準出力の差し替えが run と混ざる）
            done = subprocess.run([sys.executable, str(SCRIPT), *argv], capture_output=True, check=True)
            return done.stdout.decode("utf-8").replace("\r\n", "\n")

        def app():
            deadline = time.time() + 10
            while time.time() < deadline:
                out = app_cli("next", "--dir", str(self.work))
                if out != "なし\n":
                    seen.append(out)
                    job_id = out.split()[1]
                    (self.box / "tmp" / f"{job_id}.report.md").write_text("テストを 2 件足しました\n", encoding="utf-8")
                    seen.append(app_cli("done", "--dir", str(self.work), "--id", job_id, "--status", "ok"))
                    return
                time.sleep(0.05)

        thread = threading.Thread(target=app)
        thread.start()
        code, out, _ = self.run_cli(
            "run", "--dir", str(self.work), "--prompt", "テストを足して", "--timeout", "10", "--poll", "0.05", "--settle", "0.2"
        )
        thread.join()
        self.assertEqual(code, job.EXIT_OK)
        self.assertEqual(out.strip(), "テストを 2 件足しました")
        self.assertTrue(seen[0].rstrip().endswith("--- 依頼 ---\nテストを足して"))
        self.assertIn("done --dir", seen[0])
        self.assertEqual(seen[1], "なし\n")  # done は次の依頼も見る
        self.assertEqual(list((self.box / "tmp").iterdir()), [])

    def test_done_rejects_an_unknown_id(self):
        code, _, err = self.run_cli("done", "--dir", str(self.work), "--id", "nope", "--status", "ok")
        self.assertEqual(code, job.EXIT_USAGE)
        self.assertIn("nope", err)

    def test_done_twice_keeps_the_first_result_and_goes_on(self):
        self.run_cli("run", "--dir", str(self.work), "--prompt", "x", "--no-wait")
        job_id = job.claim_next(self.box)
        (self.box / "tmp" / f"{job_id}.report.md").write_text("\ufeff最初の報告\n", encoding="utf-8")
        self.assertEqual(self.run_cli("done", "--dir", str(self.work), "--id", job_id, "--status", "ok")[0], job.EXIT_OK)
        code, out, err = self.run_cli("done", "--dir", str(self.work), "--id", job_id, "--status", "failed")
        self.assertEqual((code, out), (job.EXIT_OK, "なし\n"))
        self.assertIn(job_id, err)
        self.assertEqual(job.parse_result((self.box / "results" / f"{job_id}.md").read_text(encoding="utf-8")), ("ok", "最初の報告"))

    def test_done_rejects_an_id_that_points_outside_the_box(self):
        code, _, err = self.run_cli("done", "--dir", str(self.work), "--id", "../x", "--status", "ok")
        self.assertEqual(code, job.EXIT_USAGE)
        self.assertIn("../x", err)

    def test_app_side_commands_reject_a_missing_dir_without_creating_it(self):
        missing = self.work / "nope"
        for argv in (["prompt"], ["next"], ["status"], ["done", "--id", "x", "--status", "ok"]):
            code, out, err = self.run_cli(*argv, "--dir", str(missing))
            self.assertEqual((code, out), (job.EXIT_USAGE, ""), argv)
            self.assertIn("nope", err)
        self.assertFalse(missing.exists())

    def test_prompt_file_with_bom_and_stdin_are_read_as_utf8(self):
        source = self.work / "p.md"
        source.write_bytes("\ufeff誤字を直して\n".encode("utf-8"))
        _, out, _ = self.run_cli("run", "--dir", str(self.work), "--prompt-file", str(source), "--no-wait")
        self.assertEqual((self.box / "pending" / f"{out.strip()}.md").read_text(encoding="utf-8"), "誤字を直して\n")
        done = subprocess.run(
            [sys.executable, str(SCRIPT), "run", "--dir", str(self.work), "--no-wait"],
            input="標準入力から".encode("utf-8"), capture_output=True, check=True, env={**os.environ, "PYTHONIOENCODING": "cp932"},
        )
        job_id = done.stdout.decode("utf-8").strip()
        self.assertEqual((self.box / "pending" / f"{job_id}.md").read_text(encoding="utf-8"), "標準入力から\n")
        code, _, err = self.run_cli("run", "--dir", str(self.work), "--prompt-file", str(self.work / "none.md"))
        self.assertEqual(code, job.EXIT_USAGE)
        self.assertIn("none.md", err)

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
