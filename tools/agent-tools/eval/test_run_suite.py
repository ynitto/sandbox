#!/usr/bin/env python3
"""run_suite の条件透過（計画 2026-08-22 §4.2 A3）。LLM も worker_eval も呼ばない（dry-run）。

測りたいのは「腕の条件が worker_eval へ届き、manifest に同じ語で残る」こと。
届かなければ条件の違う数字が同じ表へ並び、残らなければ台帳から条件を復元できない。
"""
import json
import pathlib
import subprocess
import sys
import tempfile
import unittest

HERE = pathlib.Path(__file__).resolve().parent


def _dry_run(*extra: str) -> tuple[dict, str]:
    with tempfile.TemporaryDirectory() as tmp:
        subprocess.run([sys.executable, str(HERE / "run_suite.py"), "--model", "m",
                        "--cli", "aider", "--units", "worker", "--dry-run",
                        "--results-dir", tmp, *extra],
                       check=True, capture_output=True, text=True)
        run = next(pathlib.Path(tmp).iterdir())
        manifest = json.loads((run / "manifest.json").read_text(encoding="utf-8"))
        command = (run / "worker" / "command.txt").read_text(encoding="utf-8")
    return manifest, command


class WorkerArmPassThroughTest(unittest.TestCase):
    def test_unspecified_arm_adds_nothing(self):
        # 未指定は 1 バイトも足さない——worker_eval の「未宣言」と「既定値を明示」の区別を壊さない。
        manifest, command = _dry_run()
        self.assertEqual(manifest["worker_arm"], {})
        for flag in ("--tasks", "--agent-policy", "--temperature", "--resample", "--num-ctx"):
            self.assertNotIn(flag, command)

    def test_specified_arm_reaches_worker_eval_and_manifest(self):
        manifest, command = _dry_run("--tasks", "T1gate,T3gate", "--agent-policy", "off",
                                     "--temperature", "1.0", "--top-p", "0.95",
                                     "--top-k", "64", "--resample", "3")
        self.assertEqual(manifest["worker_arm"],
                         {"--tasks": "T1gate,T3gate", "--agent-policy": "off",
                          "--temperature": 1.0, "--top-p": 0.95, "--top-k": 64,
                          "--resample": 3})
        for flag, value in manifest["worker_arm"].items():
            self.assertIn(f"{flag} {value}", command)


if __name__ == "__main__":
    unittest.main()
