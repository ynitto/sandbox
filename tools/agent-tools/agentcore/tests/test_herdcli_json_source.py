"""herdcli の JSON 引数（パスか JSON 本体か）の見分け。

長い JSON 文字列をそのまま渡すと Path.is_file() が OSError（名前が長すぎる）を投げ、
CI の python（agentcore）ジョブが落ちていた。パスとして見られない値は JSON 本体として扱う。

    python3 -m pytest tools/agent-tools/agentcore/tests/test_herdcli_json_source.py
"""
import json
import tempfile
import unittest
from pathlib import Path

from agentcore import herdcli


class JsonSourceTests(unittest.TestCase):
    def test_long_json_string_is_returned_as_is(self):
        raw = json.dumps({"candidates": ["x" * 50 for _ in range(200)]})
        self.assertGreater(len(raw), 4096)
        self.assertEqual(herdcli._json_source(raw), raw)

    def test_short_json_string_is_returned_as_is(self):
        self.assertEqual(herdcli._json_source('{"a": 1}'), '{"a": 1}')

    def test_path_to_file_returns_its_contents(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "in.json"
            path.write_text('{"b": 2}', encoding="utf-8")
            self.assertEqual(herdcli._json_source(str(path)), '{"b": 2}')


if __name__ == "__main__":
    unittest.main()
