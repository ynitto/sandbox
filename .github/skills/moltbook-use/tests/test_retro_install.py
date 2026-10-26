"""retro_install.py（Copilot アプリ・kiro-cli への組み込み）の回帰テスト。

設計: docs/designs/moltbook-retro-design.md §9
外部コマンド（kiro-cli・copilot・pip）は呼ばず、置き場所は一時フォルダ（MOLTBOOK_AGENT_HOME / HOME）に向ける。
実行: python3 -m unittest discover -s .github/skills/moltbook-use/tests
"""
from __future__ import annotations

import importlib.util
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

SCRIPTS = Path(__file__).resolve().parent.parent / "scripts"
spec = importlib.util.spec_from_file_location("retro_install", SCRIPTS / "retro_install.py")
ri = importlib.util.module_from_spec(spec)
spec.loader.exec_module(ri)


class _Home(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.env = mock.patch.dict(os.environ, {"MOLTBOOK_AGENT_HOME": str(self.root / "agent"),
                                                "MOLTBOOK_HOME": str(self.root / "state"),
                                                "MOLTBOOK_URL": "", "MOLTBOOK_TOKEN": ""})
        self.env.start()
        self.settings: dict = {}
        self.commands: list = []
        self.run = mock.patch.object(ri, "_run", side_effect=self.fake_run)
        self.run.start()
        self.which = mock.patch.object(ri.shutil, "which", return_value=None)
        self.which.start()

    def tearDown(self):
        self.which.stop()
        self.run.stop()
        self.env.stop()
        self.tmp.cleanup()

    def fake_run(self, cmd, timeout=60, **kw):
        self.commands.append(cmd)
        if cmd[:2] == ["kiro-cli", "settings"]:
            if cmd[2] == "--delete":
                self.settings.pop(cmd[3], None)
                return 0, ""
            if len(cmd) == 4:
                self.settings[cmd[2]] = cmd[3]
                return 0, ""
            return 0, self.settings.get(cmd[2], "null")
        return 1, "not available"

    def agents(self) -> Path:
        return self.root / "agent" / "agents"


class KiroTest(_Home):
    def test_builtin_default_gets_a_moltbook_agent_and_is_restored(self):
        rec: dict = {}
        ri.install_kiro(rec, v3=False)
        agent = json.loads((self.agents() / "moltbook.json").read_text(encoding="utf-8"))
        self.assertEqual(agent["tools"], ["*"])
        self.assertEqual(sorted(agent["hooks"]), sorted(ri.KIRO_V2_HOOKS))
        self.assertIn("moltbook_hook.py\" prompt --client kiro-v2", agent["hooks"]["userPromptSubmit"][0]["command"])
        self.assertEqual(self.settings["chat.defaultAgent"], "moltbook")
        ri.uninstall_kiro(rec)
        self.assertFalse((self.agents() / "moltbook.json").exists())
        self.assertNotIn("chat.defaultAgent", self.settings)

    def test_user_agent_gets_hooks_appended_and_only_ours_are_removed(self):
        self.settings["chat.defaultAgent"] = "mine"
        mine = {"name": "mine", "tools": ["fs_read"],
                "hooks": {"userPromptSubmit": [{"command": "echo mine"}], "stop": [{"command": "notify"}]}}
        self.agents().mkdir(parents=True)
        (self.agents() / "mine.json").write_text(json.dumps(mine), encoding="utf-8")
        rec: dict = {}
        ri.install_kiro(rec, v3=False)
        ri.install_kiro(rec, v3=False)   # 2 回入れても重複しない
        agent = json.loads((self.agents() / "mine.json").read_text(encoding="utf-8"))
        self.assertEqual([h["command"] for h in agent["hooks"]["userPromptSubmit"]][0], "echo mine")
        self.assertEqual(len(agent["hooks"]["userPromptSubmit"]), 2)
        self.assertEqual(self.settings["chat.defaultAgent"], "mine")
        backups = list((self.root / "state" / "backup").iterdir())
        self.assertEqual(len(backups), 1, "控えは最初の 1 回だけ")
        self.assertEqual(json.loads(backups[0].read_text(encoding="utf-8")), mine)
        ri.uninstall_kiro(rec)
        self.assertEqual(json.loads((self.agents() / "mine.json").read_text(encoding="utf-8")), mine)

    def test_v3_global_hooks(self):
        rec: dict = {}
        ri.install_kiro(rec, v3=True)
        files = sorted(Path(f).name for f in rec["v3_hooks"])
        self.assertEqual(files, sorted(f"moltbook-retro-{t}.json" for t in ri.KIRO_V3_HOOKS))
        stop = json.loads((self.root / "agent" / "hooks" / "moltbook-retro-Stop.json").read_text(encoding="utf-8"))
        self.assertIn("stop --client kiro-v3", stop["hooks"][0]["action"]["command"])
        ri.uninstall_kiro(rec)
        self.assertEqual(list((self.root / "agent" / "hooks").iterdir()), [])


class CopilotTest(_Home):
    def test_plugin_and_marketplace(self):
        rec: dict = {}
        notes = ri.install_copilot(rec)
        root = Path(rec["marketplace"])
        hooks = json.loads((root / "plugins" / "moltbook-retro" / "hooks.json").read_text(encoding="utf-8"))
        self.assertEqual(sorted(hooks["hooks"]), sorted(ri.COPILOT_HOOKS))
        failure = hooks["hooks"]["postToolUseFailure"][0]
        self.assertTrue(failure["powershell"].startswith("& \""))
        self.assertTrue(failure["powershell"].endswith("tool --failed --client copilot"))
        market = json.loads((root / ".github" / "plugin" / "marketplace.json").read_text(encoding="utf-8"))
        self.assertEqual(market["plugins"][0]["source"], "./plugins/moltbook-retro")
        self.assertIn("Customize", notes[-1])
        ri.uninstall_copilot(rec)
        self.assertFalse(root.exists())


class InstallTest(_Home):
    def test_install_copies_skill_without_tests_and_records(self):
        with mock.patch.object(ri, "ensure_yaml", return_value=True), mock.patch.object(ri, "doctor", return_value=0), \
                mock.patch("builtins.print"), mock.patch.object(ri.sys.stdin, "isatty", return_value=False):
            self.assertEqual(ri.main(["install", "--client", "kiro"]), 0)
        skill = self.root / "agent" / "skills" / "moltbook-use"
        self.assertTrue((skill / "scripts" / "moltbook_hook.py").is_file())
        self.assertFalse((skill / "tests").exists())
        rec = json.loads((self.root / "state" / "install.json").read_text(encoding="utf-8"))
        self.assertEqual(rec["agent"], "moltbook")


if __name__ == "__main__":
    unittest.main()
