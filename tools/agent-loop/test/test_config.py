"""agent-loop 設定読み込みの回帰テスト。"""
import os
import sys
import tempfile
import unittest
from pathlib import Path

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


if __name__ == "__main__":
    unittest.main()
