#!/usr/bin/env python3
import pathlib
import sys
import unittest
from unittest import mock

HERE = pathlib.Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))
import agent_loop as al  # noqa: E402


class SendChatToPaneTest(unittest.TestCase):
    def _calls(self, text):
        completed = mock.Mock(returncode=0, stderr="")
        with mock.patch.object(al, "_tmux_cmd", return_value=completed) as tmux, \
             mock.patch.object(al.time, "sleep") as sleep:
            self.assertEqual(al._send_chat_to_pane("%1", text), (True, ""))
        return [call.args for call in tmux.call_args_list], sleep

    def test_codex_skill_gets_completion_and_submit_enter(self):
        calls, sleep = self._calls("$ponytail full")
        self.assertEqual(calls[0], ("send-keys", "-t", "%1", "-l", "--", "$ponytail full"))
        self.assertEqual(calls.count(("send-keys", "-t", "%1", "Enter")), 2)
        self.assertEqual([call.args for call in sleep.call_args_list], [(3,), (3,), (3,)])

    def test_plain_chat_gets_one_submit_enter(self):
        calls, sleep = self._calls("ドキュメントを読んで\n要約して")
        self.assertEqual(
            calls[0],
            ("send-keys", "-t", "%1", "-l", "--", "ドキュメントを読んで 要約して"),
        )
        self.assertEqual(calls.count(("send-keys", "-t", "%1", "Enter")), 1)
        self.assertEqual([call.args for call in sleep.call_args_list], [(3,), (3,)])


if __name__ == "__main__":
    unittest.main()
