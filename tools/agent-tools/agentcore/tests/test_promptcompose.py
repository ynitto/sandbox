"""agentcore.promptcompose の単体テスト（案 H・プロンプトキャッシュに適合する注入順の正規化）。
設計: docs/plans/2026-08-05-phase1-token-efficiency-detailed-design.md §3

    python -m unittest discover -s tools/agent-tools/agentcore/tests
"""
from __future__ import annotations

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from agentcore import promptcompose  # noqa: E402


class ComposeTests(unittest.TestCase):
    def test_stable_then_variable_order(self):
        out = promptcompose.compose(["charter", "rules"], ["task title", "criteria"])
        self.assertEqual(out, "charter\n\nrules\n\ntask title\n\ncriteria")

    def test_empty_blocks_are_dropped(self):
        out = promptcompose.compose(["charter", "", None, "  ", "rules"], ["task"])
        self.assertEqual(out, "charter\n\nrules\n\ntask")

    def test_no_stable_matches_variable_only_join(self):
        """stable が空なら variable だけを連結した結果と 1 バイトも変わらない
        （stable_prefix を使わない従来の組み立てと同一になることの保証）。"""
        out_with_empty_stable = promptcompose.compose([], ["task title", "criteria"])
        out_variable_only = "\n\n".join(["task title", "criteria"])
        self.assertEqual(out_with_empty_stable, out_variable_only)

    def test_no_stable_no_variable_is_empty_string(self):
        self.assertEqual(promptcompose.compose([], []), "")
        self.assertEqual(promptcompose.compose(None, None), "")

    def test_only_stable_no_trailing_separator(self):
        out = promptcompose.compose(["charter"], [])
        self.assertEqual(out, "charter")

    def test_whitespace_only_blocks_do_not_leave_gaps(self):
        out = promptcompose.compose(["a"], ["  \n  ", "b"])
        self.assertEqual(out, "a\n\nb")

    def test_deterministic_same_input_same_output(self):
        stable, variable = ["charter text", "rules text"], ["task", "criteria"]
        self.assertEqual(promptcompose.compose(stable, variable), promptcompose.compose(stable, variable))

    def test_does_not_mutate_input_lists(self):
        stable, variable = ["a", ""], ["b"]
        promptcompose.compose(stable, variable)
        self.assertEqual(stable, ["a", ""])
        self.assertEqual(variable, ["b"])

    def test_two_different_tasks_share_the_stable_prefix(self):
        """本体の目的そのもの: 同一プロジェクトの異なる 2 タスクでも、stable が同じなら
        出力は共通のプレフィックスを持つ（キャッシュがここに乗る）。"""
        stable = ["charter（不変）", "rules.md（不変）"]
        out1 = promptcompose.compose(stable, ["タスクA: 認証を直す"])
        out2 = promptcompose.compose(stable, ["タスクB: 決済を直す"])
        common = "charter（不変）\n\nrules.md（不変）"
        self.assertTrue(out1.startswith(common))
        self.assertTrue(out2.startswith(common))
        self.assertNotEqual(out1, out2)

    def test_changing_stable_changes_the_prefix(self):
        """rules.md が変われば安定部も変わる（安定部が正しく「プロジェクト内で不変」を
        表していることの裏付け——中身を勝手に固定しない）。"""
        out_v1 = promptcompose.compose(["charter", "rules v1"], ["task"])
        out_v2 = promptcompose.compose(["charter", "rules v2"], ["task"])
        self.assertTrue(out_v1.startswith("charter"))
        self.assertTrue(out_v2.startswith("charter"))
        self.assertNotEqual(out_v1, out_v2)


if __name__ == "__main__":
    unittest.main()
