#!/usr/bin/env python3
"""打ち切りは**孫まで**届くか（`agentcore.procgroup`）。

直接の子だけを殺すと、その下の推論クライアントは生き残ってパイプを握り続ける。
実測 2026-08-29: 上限 900 秒の実行が 70 分走り、殺したはずの ollama が朝まで
GPU を占めて次の実行が順番待ちになった。ここで見るのは 1 点だけ——**打ち切った
あとに孫が残らないこと**。
"""
import os
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))
from agentcore import procgroup  # noqa: E402
from agentcore.harness import toolloop as tl  # noqa: E402

# 孫（sleep）を起こし、その pid を書いてから待つ。孫は親のパイプを握ったままなので、
# 親だけを殺しても communicate() は EOF を待ち続ける（これが再現したい形）。
SPAWN = "sleep 300 & echo $! > '{pidfile}'; wait"


def _grandchild_pid(pidfile: Path, timeout: float = 5.0) -> int:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        text = pidfile.read_text().strip() if pidfile.exists() else ""
        if text:
            return int(text)
        time.sleep(0.05)
    raise AssertionError("孫が起動しなかった（テストの前提が崩れている）")


def _alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except OSError:
        return False
    return True


def _wait_dead(pid: int, timeout: float = 10.0) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if not _alive(pid):
            return True
        time.sleep(0.05)
    return False


class GrandchildKillTests(unittest.TestCase):
    def setUp(self):
        if os.name != "posix":
            self.skipTest("プロセスグループは POSIX のみ")
        self.tmp = tempfile.TemporaryDirectory()
        self.pidfile = Path(self.tmp.name) / "grandchild.pid"
        self.pid = None

    def tearDown(self):
        if self.pid and _alive(self.pid):
            os.kill(self.pid, 9)        # 落とし損ねた孫を残さない
        self.tmp.cleanup()

    def _argv(self):
        return ["/bin/sh", "-c", SPAWN.format(pidfile=self.pidfile)]

    def test_run_timeout_kills_grandchild(self):
        """`procgroup.run` の上限。孫が握っていても待ち続けない。"""
        started = time.monotonic()
        with self.assertRaises(subprocess.TimeoutExpired):
            procgroup.run(self._argv(), capture_output=True, text=True, timeout=1.0)
        self.pid = _grandchild_pid(self.pidfile)
        self.assertTrue(_wait_dead(self.pid), "孫が生き残った")
        self.assertLess(time.monotonic() - started, 40, "上限が効いていない")

    def _watched(self, *, idle_sec: float, ceiling: float | None = None):
        original = tl._TL_AGENT_WALL_CEILING_SEC
        if ceiling is not None:
            tl._TL_AGENT_WALL_CEILING_SEC = ceiling
        try:
            return tl._tl_run_watched(self._argv(), cwd=self.tmp.name, env=dict(os.environ),
                                      stdin=None, idle_sec=idle_sec, beacon_path="")
        finally:
            tl._TL_AGENT_WALL_CEILING_SEC = original

    def test_idle_timeout_kills_grandchild(self):
        result = self._watched(idle_sec=1.0)
        self.pid = _grandchild_pid(self.pidfile)
        self.assertIn("無進捗", result["error"])
        self.assertTrue(_wait_dead(self.pid), "無進捗打ち切りで孫が生き残った")

    def test_ceiling_kills_grandchild(self):
        result = self._watched(idle_sec=600.0, ceiling=1.0)
        self.pid = _grandchild_pid(self.pidfile)
        self.assertIn("天井", result["error"])
        self.assertTrue(_wait_dead(self.pid), "天井打ち切りで孫が生き残った")


if __name__ == "__main__":
    unittest.main()
