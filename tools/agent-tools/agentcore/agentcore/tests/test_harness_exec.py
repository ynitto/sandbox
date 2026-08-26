"""ハーネスの子プロセス実行——**無進捗でだけ打ち切る**ことを縛る。

エージェント CLI の 1 呼び出しは、ローカル推論では数十分かかることが正常で、ollama が
他リクエストで塞がっていれば順番待ちがそこへ積み上がる。壁時計で切ると、正常に進んで
いる実行と、順番を待っているだけの実行を、ハングと同じ扱いで殺す（しかも子は SIGKILL
されるので「queue で待っていた」という証跡がどこにも残らない）。

一方でモデルが要求した `run`（宣言した秒で終わるべきもの）は壁時計のままである必要が
ある——`sleep 9999` は宣言どおり切られるのが正しい。その 2 つを分けているのが `idle`。
"""
from __future__ import annotations

import os
import sys
import tempfile
import time
import unittest
from pathlib import Path

from agentcore.harness import toolloop


def _python(script: str) -> "list[str]":
    return [sys.executable, "-c", script]


class TestIdleTimeout(unittest.TestCase):
    def _exec(self, argv, *, timeout_sec, idle, log_file, cwd=None):
        return toolloop._tl_exec_argv(argv[0], argv[1:], cwd=cwd or os.getcwd(),
                                      timeout_sec=timeout_sec, log_file=log_file,
                                      idle=idle)

    def test_silent_child_is_killed_after_the_idle_limit(self):
        with tempfile.TemporaryDirectory() as tmp:
            log = str(Path(tmp) / "harness.jsonl")
            started = time.monotonic()
            result = self._exec(_python("import time; time.sleep(30)"),
                                timeout_sec=1.0, idle=True, log_file=log)
        self.assertIn("進まない", result["error"])
        self.assertLess(time.monotonic() - started, 15.0, "上限で打ち切れていない")
        # 壁時計で切っていた頃と同じく、制御応答の再試行に拾われる文言であること。
        self.assertTrue(toolloop._TL_TRANSIENT_RE.search(result["error"]),
                        "無進捗の打ち切りが一時障害として読めない")

    def test_a_child_that_keeps_printing_is_not_killed(self):
        """出力が来ている限り待つ。壁時計なら切られていた長さを通す。"""
        script = ("import sys, time\n"
                  "for _ in range(6):\n"
                  "    sys.stderr.write('.'); sys.stderr.flush(); time.sleep(0.3)\n"
                  "sys.stdout.write('done')\n")
        with tempfile.TemporaryDirectory() as tmp:
            log = str(Path(tmp) / "harness.jsonl")
            result = self._exec(_python(script), timeout_sec=1.0, idle=True, log_file=log)
        self.assertEqual(result["status"], 0)
        self.assertEqual(result["stdout"], "done")
        self.assertEqual(result["error"], "")

    def test_a_silent_child_that_marks_the_beacon_is_not_killed(self):
        """1 バイトも出力しない子でも、灯台を刻んでいれば生きていると読む。

        ヘッドレスのローカル推論がまさにこれ（終わるまで stdout に何も出さない）。
        灯台の場所は `AGENT_PROGRESS_BEACON` で子へ渡している。
        """
        script = ("import os, time\n"
                  "beacon = os.environ['AGENT_PROGRESS_BEACON']\n"
                  "for i in range(6):\n"
                  "    open(beacon, 'w').write(str(i))\n"
                  "    time.sleep(0.3)\n"
                  "print('done', end='')\n")
        with tempfile.TemporaryDirectory() as tmp:
            log = str(Path(tmp) / "harness.jsonl")
            result = self._exec(_python(script), timeout_sec=1.0, idle=True, log_file=log)
            leftovers = [p.name for p in Path(tmp).iterdir() if "beacon" in p.name]
        self.assertEqual(result["status"], 0, result["error"])
        self.assertEqual(result["stdout"], "done")
        self.assertEqual(leftovers, [], "灯台は実行が終わったら捨てる（記録ではない）")

    def test_a_child_that_only_marks_the_beacon_then_stops_is_killed(self):
        """灯台が止まれば打ち切る——「刻み続けている限り」であって無条件ではない。"""
        script = ("import os, time\n"
                  "open(os.environ['AGENT_PROGRESS_BEACON'], 'w').write('x')\n"
                  "time.sleep(30)\n")
        with tempfile.TemporaryDirectory() as tmp:
            log = str(Path(tmp) / "harness.jsonl")
            started = time.monotonic()
            result = self._exec(_python(script), timeout_sec=1.0, idle=True, log_file=log)
        self.assertIn("進まない", result["error"])
        self.assertLess(time.monotonic() - started, 15.0)

    def test_output_before_the_kill_is_still_returned(self):
        """打ち切っても、そこまでに受け取った出力は捨てない（失敗の手掛かりになる）。"""
        script = ("import sys, time\n"
                  "sys.stderr.write('途中まで'); sys.stderr.flush(); time.sleep(30)\n")
        with tempfile.TemporaryDirectory() as tmp:
            log = str(Path(tmp) / "harness.jsonl")
            result = self._exec(_python(script), timeout_sec=1.0, idle=True, log_file=log)
        self.assertIn("途中まで", result["stderr"])
        self.assertIn("進まない", result["error"])

    def test_stdin_reaches_the_child(self):
        script = "import sys; sys.stdout.write(sys.stdin.read().upper())"
        with tempfile.TemporaryDirectory() as tmp:
            log = str(Path(tmp) / "harness.jsonl")
            result = toolloop._tl_exec_argv(
                sys.executable, ["-c", script], cwd=os.getcwd(), timeout_sec=10.0,
                stdin="ohayou", log_file=log, idle=True)
        self.assertEqual(result["stdout"], "OHAYOU")
        self.assertEqual(result["status"], 0)

    def test_missing_command_is_an_error_not_a_crash(self):
        with tempfile.TemporaryDirectory() as tmp:
            log = str(Path(tmp) / "harness.jsonl")
            result = self._exec(["この実行ファイルは無い"], timeout_sec=2.0, idle=True,
                                log_file=log)
        self.assertIsNone(result["status"])
        self.assertTrue(result["error"])


class TestWallClockStaysForDeclaredRuns(unittest.TestCase):
    def test_a_run_request_is_still_cut_by_the_wall_clock(self):
        """`idle=False`（既定）は従来どおり壁時計。出力が来ていても宣言した秒で切る。"""
        script = ("import sys, time\n"
                  "while True:\n"
                  "    sys.stderr.write('.'); sys.stderr.flush(); time.sleep(0.1)\n")
        with tempfile.TemporaryDirectory() as tmp:
            log = str(Path(tmp) / "harness.jsonl")
            started = time.monotonic()
            result = toolloop._tl_exec_argv(
                sys.executable, ["-c", script], cwd=os.getcwd(), timeout_sec=1.0,
                log_file=log)
        self.assertIn("タイムアウト", result["error"])
        self.assertLess(time.monotonic() - started, 15.0)

    def test_the_beacon_is_only_handed_to_idle_watched_children(self):
        script = ("import os, sys\n"
                  "sys.stdout.write(os.environ.get('AGENT_PROGRESS_BEACON', 'なし'))\n")
        with tempfile.TemporaryDirectory() as tmp:
            log = str(Path(tmp) / "harness.jsonl")
            plain = toolloop._tl_exec_argv(sys.executable, ["-c", script],
                                           cwd=os.getcwd(), timeout_sec=10.0, log_file=log)
            watched = toolloop._tl_exec_argv(sys.executable, ["-c", script],
                                             cwd=os.getcwd(), timeout_sec=10.0,
                                             log_file=log, idle=True)
        self.assertEqual(plain["stdout"], "なし")
        self.assertNotEqual(watched["stdout"], "なし")


if __name__ == "__main__":
    unittest.main()
