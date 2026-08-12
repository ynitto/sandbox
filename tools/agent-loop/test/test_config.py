"""agent-loop 設定読み込みの回帰テスト。"""
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
import agent_loop as al  # noqa: E402


class JsoncConfigTests(unittest.TestCase):
    def test_multiple_prompts_and_trailing_commas(self):
        with tempfile.TemporaryDirectory() as tmp:
            settings = Path(tmp, ".vscode", "settings.json")
            settings.parent.mkdir()
            settings.write_text('''{
              "agentExecutor.periodicPrompts": [
                {"agentId": "kiro", "prompt": "one", "intervalMinutes": 1},
                {"agentId": "kiro", "prompt": "two", "intervalMinutes": 2},
              ],
            }''', encoding="utf-8")
            self.assertEqual(
                [p["prompt"] for p in al.load_vscode_periodic_prompts(Path(tmp))],
                ["one", "two"],
            )


class PromptConfigTests(unittest.TestCase):
    def test_mapping_lookup_expands_prompt_and_cwd(self):
        with tempfile.TemporaryDirectory() as tmp:
            config = Path(tmp, al.AGENT_HOME, "agent-loop.json")
            config.parent.mkdir()
            config.write_text('''{
              "mapping": {
                "workspace": {"root": "/tmp/project"},
                "messages": {"review": "Review the changes"}
              },
              "prompts": [{
                "prompt": "{{lookup messages review}}",
                "cwd": "{{lookup workspace root}}"
              }]
            }''', encoding="utf-8")

            self.assertEqual(al.load_prompt_config(tmp), [{
                "prompt": "Review the changes",
                "cwd": "/tmp/project",
            }])

    def test_user_home_does_not_read_dot_agent(self):
        with tempfile.TemporaryDirectory() as tmp:
            old = Path(tmp, ".agent", "agent-loop.json")
            old.parent.mkdir()
            old.write_text('{"prompts": [{"name": "old"}]}', encoding="utf-8")
            with mock.patch.object(al.Path, "home", return_value=Path(tmp)):
                self.assertEqual(al._load_prompt_file_data(tmp), {})
                self.assertEqual(al._prompt_file(tmp),
                                 Path(tmp, ".agents", "agent-loop.yml"))

    def test_save_updates_existing_yaml_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            config = Path(tmp, al.AGENT_HOME, "agent-loop.yaml")
            config.parent.mkdir()
            config.write_text("kiro_options:\n  model: test\nprompts: []\n", encoding="utf-8")

            prompts = [{"name": "saved", "prompt": "hello", "interval_minutes": 5}]
            self.assertTrue(al.save_prompt_config(tmp, prompts))

            self.assertFalse(config.with_suffix(".yml").exists())
            self.assertEqual(al.load_prompt_config(tmp), prompts)
            self.assertEqual(al._load_prompt_file_data(tmp)["kiro_options"], {"model": "test"})


if __name__ == "__main__":
    unittest.main()
