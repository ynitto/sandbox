"""hook_install.py（Claude Code の settings.json・kiro のエージェントへの足し引き）のテスト。"""
import io
import json
import os
import sys
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))
import hook_install as inst  # noqa: E402


class ClaudeHooksTest(unittest.TestCase):
    def test_add_is_idempotent_and_keeps_user_hooks(self):
        user = {"type": "command", "command": "echo mine"}
        settings = {"model": "x", "hooks": {"Stop": [{"hooks": [user]}]}}
        once = inst.add_claude_hooks(json.loads(json.dumps(settings)))
        twice = inst.add_claude_hooks(json.loads(json.dumps(once)))
        self.assertEqual(once, twice)
        self.assertEqual(len(twice["hooks"]["Stop"]), 2)
        self.assertEqual(twice["hooks"]["PostToolUse"][0]["matcher"], "*")
        self.assertIn("tool --failed --client claude", twice["hooks"]["PostToolUseFailure"][0]["hooks"][0]["command"])
        removed = inst.remove_claude_hooks(twice)
        self.assertEqual(removed, settings)

    def test_remove_all_drops_hooks_key(self):
        self.assertEqual(inst.remove_claude_hooks(inst.add_claude_hooks({"a": 1})), {"a": 1})


class KiroHooksTest(unittest.TestCase):
    def test_add_and_remove_keep_other_marks(self):
        other = {"command": '"py" "moltbook_hook.py" stop --client kiro-v2'}
        agent = {"name": "moltbook", "hooks": {"stop": [other]}}
        added = inst.add_kiro_hooks(json.loads(json.dumps(agent)), inst.KIRO_V2_HOOKS, "kiro-v2")
        self.assertEqual(len(added["hooks"]["stop"]), 2)
        self.assertEqual(inst.add_kiro_hooks(json.loads(json.dumps(added)), inst.KIRO_V2_HOOKS, "kiro-v2"), added)
        self.assertEqual(inst.remove_marked(added["hooks"]), {"stop": [other]})


class InstallFlowTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        t = Path(self.tmp.name)
        self.agent = t / ".claude"
        self.vault = t / "vault"
        self.vault.mkdir()
        self.patch = mock.patch.dict(os.environ, {"OBSIDIAN_USE_AGENT_HOME": str(self.agent),
                                                  "OBSIDIAN_CLI": str(t / "none")})
        self.patch.start()

    def tearDown(self):
        self.patch.stop()
        self.tmp.cleanup()

    def test_claude_install_doctor_uninstall(self):
        settings = self.agent / "settings.json"
        settings.parent.mkdir(parents=True)
        settings.write_text(json.dumps({"permissions": {"allow": ["Bash(ls)"]}}), encoding="utf-8")
        buf = io.StringIO()
        with redirect_stdout(buf):
            code = inst.main(["install", "--client", "claude", "--vault", str(self.vault)])
        self.assertEqual(code, 0, buf.getvalue())
        self.assertTrue((self.agent / "skills" / "obsidian-use" / "scripts" / "obsidian_hook.py").is_file())
        self.assertFalse((self.agent / "skills" / "obsidian-use" / "tests").exists())
        self.assertTrue((self.vault / "llm-wiki" / "index.base").is_file())
        data = json.loads(settings.read_text(encoding="utf-8"))
        self.assertEqual(set(data["hooks"]), set(inst.CLAUDE_HOOKS))
        self.assertIn(str(self.agent / "skills" / "obsidian-use"), data["hooks"]["Stop"][0]["hooks"][0]["command"])
        with redirect_stdout(io.StringIO()):
            inst.main(["uninstall", "--client", "claude"])
        self.assertEqual(json.loads(settings.read_text(encoding="utf-8")), {"permissions": {"allow": ["Bash(ls)"]}})


if __name__ == "__main__":
    unittest.main()
