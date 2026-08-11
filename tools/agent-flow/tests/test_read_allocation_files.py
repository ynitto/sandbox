"""読込割付のパスが argv へも回ること。

aider は「チャットに入っているファイルしか編集しない」ので、本文の【読込割付】だけでは
着手できない（実測 2026-08-11: 6 run が「ファイルを追加してくれ」で終わった）。
`file_flag` を宣言していない CLI の起動形は変えない、が同時に守るべき不変条件。
"""
import sys
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import agent_flow  # noqa: E402


class ReadAllocationFilesTests(unittest.TestCase):
    def _argv_for(self, cli: str) -> list:
        seen = {}

        def fake_run(cmd, **kwargs):
            seen["argv"] = cmd
            return mock.Mock(returncode=0, stdout="done", stderr="")

        with mock.patch.object(agent_flow, "_agent_for", return_value=(cli, "qwen3.5:9b")), \
             mock.patch.object(agent_flow.subprocess, "run", side_effect=fake_run), \
             mock.patch.object(agent_flow, "_flow_worker_prompt", return_value=None):
            agent_flow.execute_agent(
                "work", "billing を直す", {}, "qwen3.5:9b",
                read_allocation=[{"path": "eval/billing.py", "reason": "直す対象"}])
        return seen["argv"]

    def test_aider_receives_the_allocated_paths(self):
        argv = self._argv_for("aider")
        self.assertIn("--file", argv)
        self.assertEqual(argv[argv.index("--file") + 1], "eval/billing.py")

    def test_cli_without_file_flag_is_unchanged(self):
        argv = self._argv_for("ollama")
        self.assertNotIn("--file", argv)
        self.assertNotIn("eval/billing.py", argv)


if __name__ == "__main__":
    unittest.main()
