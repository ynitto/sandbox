"""読込割付のパスが read-only の argv へも回ること。

aider は本文の【読込割付】だけでは対象を読まないため argv に渡す必要があるが、読込割付は
編集許可ではない。`--read` へ渡して参照専用にし、`read_flag` を宣言していない CLI の
起動形は変えない。
"""
import sys
import tempfile
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

    def test_aider_receives_allocated_paths_as_read_only(self):
        argv = self._argv_for("aider")
        self.assertIn("--read", argv)
        self.assertEqual(argv[argv.index("--read") + 1], "eval/billing.py")
        self.assertNotIn("--file", argv)

    def test_cli_without_file_flag_is_unchanged(self):
        argv = self._argv_for("ollama")
        self.assertNotIn("--file", argv)
        self.assertNotIn("eval/billing.py", argv)

    def test_opt_in_symbol_slice_uses_temporary_read_file_and_records_receipt(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            source = root / "big.py"
            source.write_text(
                "\n".join([*(f"CONST_{i} = {i}" for i in range(450)),
                            "", "def target():", "    return 42", ""]),
                encoding="utf-8")
            seen = {}

            def fake_run(cmd, **kwargs):
                raw_path = Path(cmd[cmd.index("--read") + 1])
                read_path = raw_path if raw_path.is_absolute() else Path(kwargs["cwd"]) / raw_path
                seen["read_path"] = read_path
                seen["content"] = read_path.read_text(encoding="utf-8")
                return mock.Mock(returncode=0, stdout="done", stderr="")

            with mock.patch.object(agent_flow, "_agent_for", return_value=("aider", "qwen3.5:9b")), \
                 mock.patch.object(agent_flow.subprocess, "run", side_effect=fake_run), \
                 mock.patch.object(agent_flow, "_flow_worker_prompt", return_value=None):
                _output, data = agent_flow.execute_agent(
                    "work", "target を確認する", {}, "qwen3.5:9b",
                    workspace={"clone": str(root)},
                    read_allocation=[{"path": "big.py", "reason": "参照",
                                      "slice": True, "symbols": ["target"]}])

            self.assertNotEqual(seen["read_path"], source)
            self.assertIn("# agentcore.context_slice", seen["content"])
            self.assertIn("def target():", seen["content"])
            self.assertFalse(seen["read_path"].exists(), "一時抜粋は実行後に回収する")
            receipt = data["context_slices"][0]
            self.assertEqual(receipt["path"], "big.py")
            self.assertEqual(receipt["state"], "sliced")


if __name__ == "__main__":
    unittest.main()
