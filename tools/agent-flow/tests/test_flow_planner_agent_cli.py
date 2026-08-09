"""flow-planner が呼ぶエージェント CLI の argv 組み立て（LLM 無し）。

以前は組み込み 4 種の白リストで argparse が弾いており、`agents/<name>.json` を足しただけの
CLI（agent-ollama 等）を planner に指定すると **スキルが起動すらせず** agent-flow 側が黙って
縮退していた。ここで見るのは「定義ファイルのある CLI はそのまま組み立てられる」ことと、
「3 フェーズは読まない系なので readonly（道具なし）で呼ぶ」こと。
"""
from __future__ import annotations

import importlib.util
import os
import sys
import unittest

_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", ".."))
_AGENTCORE = os.path.join(_ROOT, "tools", "agent-tools", "agentcore")
if _AGENTCORE not in sys.path:
    sys.path.insert(0, _AGENTCORE)


def _load_plan():
    path = os.path.join(_ROOT, ".github", "skills", "flow-planner", "scripts", "plan.py")
    spec = importlib.util.spec_from_file_location("flow_planner_plan_cli", path)
    mod = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(mod)
    return mod


plan = _load_plan()


class AgentCmdTests(unittest.TestCase):
    def setUp(self):
        # 定義の探索は cwd/agents から始まる（agentcore.plugin_dirs）
        self._cwd = os.getcwd()
        os.chdir(_ROOT)
        self.addCleanup(lambda: os.chdir(self._cwd))

    def test_definition_only_cli_is_supported(self):
        argv, stdin, out_file = plan._agent_cmd("ollama", "qwen3.5:9b", "PROMPT")
        self.assertEqual(argv[0], "agent-ollama")
        self.assertIn("qwen3.5:9b", argv)
        self.assertEqual(stdin, "PROMPT")     # ollama は stdin 渡し
        self.assertIsNone(out_file)

    def test_readonly_drops_the_tool_loop_flags(self):
        """道具付きで呼ぶと、契約どおりの JSON 応答がツールループの規約違反として蹴られる。"""
        argv, _, _ = plan._agent_cmd("ollama", "qwen3.5:9b", "PROMPT")
        self.assertNotIn("--tools", argv)
        self.assertNotIn("bash", argv)

    def test_builtin_clis_still_build(self):
        for cli in ("kiro", "claude", "codex", "copilot"):
            argv, _, _ = plan._agent_cmd(cli, None, "PROMPT")
            self.assertTrue(argv, cli)

    def test_unknown_cli_fails_loudly(self):
        with self.assertRaises(Exception) as ctx:
            plan._agent_cmd("no-such-cli", None, "PROMPT")
        self.assertIn("no-such-cli", str(ctx.exception))


class AgentCmdWithoutAgentcoreTests(unittest.TestCase):
    """agentcore を import できない配布でも組み込み 4 種は従来どおり動く。"""

    def setUp(self):
        self._orig = plan._agentcli
        plan._agentcli = lambda: None
        self.addCleanup(lambda: setattr(plan, "_agentcli", self._orig))

    def test_builtin_fallback(self):
        argv, stdin, _ = plan._agent_cmd("claude", "opus", "PROMPT")
        self.assertEqual(argv[0], "claude")
        self.assertEqual(stdin, "PROMPT")

    def test_unknown_cli_does_not_silently_become_kiro(self):
        with self.assertRaises(RuntimeError):
            plan._agent_cmd("ollama", None, "PROMPT")


if __name__ == "__main__":
    unittest.main()
