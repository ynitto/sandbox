"""CLI 定義のファイル受け渡し（file_flag / read_flag）。

aider は「チャットに入っているファイルしか編集しない」ので、パスを argv で渡せないと
1 発起動では着手すらしない。宣言した CLI にだけ載り、他の CLI の argv は 1 トークンも
変わらないこと——ここが崩れると、既存の全 CLI の起動形が静かに変わる。
"""
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from agentcore import agentcli  # noqa: E402


class FileArgsTests(unittest.TestCase):
    def test_declared_cli_receives_paths_as_flags(self):
        spec = agentcli.load_cli("aider")
        argv = agentcli.headless_cmd(spec, "qwen3.5:9b", "直して",
                                     files=["a.py", "b.py"], read_files=["spec.py"])["argv"]
        self.assertEqual(argv[-2:], ["--message", "直して"])
        self.assertIn("--read", argv)
        self.assertEqual(argv[argv.index("--read") + 1], "spec.py")
        self.assertEqual([argv[i + 1] for i, t in enumerate(argv) if t == "--file"],
                         ["a.py", "b.py"])

    def test_undeclared_cli_argv_is_unchanged(self):
        spec = agentcli.load_cli("ollama")
        plain = agentcli.headless_cmd(spec, "qwen3.5:9b", "x")["argv"]
        with_files = agentcli.headless_cmd(spec, "qwen3.5:9b", "x",
                                           files=["a.py"], read_files=["b.py"])["argv"]
        self.assertEqual(plain, with_files)

    def test_no_paths_means_no_extra_tokens(self):
        spec = agentcli.load_cli("aider")
        plain = agentcli.headless_cmd(spec, "qwen3.5:9b", "x")["argv"]
        empty = agentcli.headless_cmd(spec, "qwen3.5:9b", "x", files=[], read_files=[])["argv"]
        self.assertEqual(plain, empty)


if __name__ == "__main__":
    unittest.main()
