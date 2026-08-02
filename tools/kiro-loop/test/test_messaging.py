"""kiro-loop / agent-loop 共有 inbox の reply_to 契約。"""
import importlib.util
import json
import tempfile
import types
import unittest
from pathlib import Path


SCRIPT = Path(__file__).resolve().parent.parent / "kiro-loop.py"


class MessagingTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        spec = importlib.util.spec_from_file_location("kiro_loop_legacy", SCRIPT)
        cls.mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(cls.mod)

    def _send(self, reply_to=None):
        with tempfile.TemporaryDirectory() as tmp:
            self.mod._AGENTS_DIR = Path(tmp)
            self.mod.cmd_msg(types.SimpleNamespace(
                to="worker", from_agent="sender", subject=None, reply_to=reply_to,
                correlation_id=None, body=["hello"]))
            files = list(Path(tmp, "worker", "inbox").glob("*.json"))
            return json.loads(files[0].read_text(encoding="utf-8"))

    def test_reply_to_is_null_when_not_replying(self):
        self.assertIsNone(self._send()["reply_to"])

    def test_reply_to_keeps_message_id(self):
        self.assertEqual(self._send("message-id")["reply_to"], "message-id")


if __name__ == "__main__":
    unittest.main()
