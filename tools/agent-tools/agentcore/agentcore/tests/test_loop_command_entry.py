"""`command:` 宣言の正規化（`agentcore.loopentry.command_spec`）。

宣言の解釈はここ 1 実装に閉じ、agent-loop のデーモン・agent-herd・dashboard がそれを
引く。共有の実装なのに、これまで試していたのは agent-loop のスイートだけだった
（入口が増えたときに、どの入口でも同じ条件であることを縛るものが無い）。

置き場は `test_loop_statemachine_entry.py` と同じこちらの根にした——同じ
`agentcore.loopentry` の宣言の読み取りを見るテストで、根が分かれていると
「entry の読み方」を探す人が両方を開くことになるため。

仕様: docs/specs/agent-loop-spec.md §2.3.2。
"""
from __future__ import annotations

import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))

from agentcore import loopentry  # noqa: E402


class CommandSpecTests(unittest.TestCase):
    """3 形（文字列 / 配列 / マップ）と、受けるキー。"""

    def test_the_three_spellings_produce_the_same_argv(self):
        argv = ["agent-audit", "calibrate", "--audit-dir", "~/.agents/audit"]
        expanded = [argv[0], argv[1], argv[2], os.path.expanduser(argv[3])]
        for declared in ("agent-audit calibrate --audit-dir ~/.agents/audit",
                         argv,
                         {"argv": argv}):
            with self.subTest(declared=declared):
                self.assertEqual(loopentry.command_spec({"command": declared})["argv"], expanded)

    def test_an_unknown_key_is_refused(self):
        # 綴り間違いを黙って無視すると、宣言したつもりの上限や許容が効かないまま回る。
        with self.assertRaisesRegex(loopentry.LoopEntryError, "知らないキー"):
            loopentry.command_spec({"command": {"argv": ["a"], "timeout_secs": 60}})

    def test_the_defaults_of_the_two_declarations_that_replace_hook_judgements(self):
        # 「この終了コードは許す」と「未導入なら飛ばす」は、書かなければ効かない。
        spec = loopentry.command_spec({"command": "echo hi"})
        self.assertEqual(spec["allow_status"], [0])
        self.assertEqual(spec["skip_if_missing"], [])
        self.assertEqual(spec["timeout_sec"], loopentry.COMMAND_TIMEOUT_SEC)
        self.assertEqual(spec["env"], {})

    def test_no_declaration_is_none(self):
        self.assertIsNone(loopentry.command_spec({"name": "x"}))
        self.assertIsNone(loopentry.command_spec({"command": "   "}))


if __name__ == "__main__":
    unittest.main()
