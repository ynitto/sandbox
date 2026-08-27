"""スラッシュ行のルータ（`agentcore.slashroute`）。

設計: docs/plans/2026-08-27-agent-herd-cloud-cli-parity-slash-dispatch-design.md §3.2。

見るのは 2 つ。①切り出しと適用の規約そのもの、②**3 か所に散っていた解釈がここへ
畳まれたこと**——`ollama_skills` の切り出し・`ollama_tui` のローカルコマンド表・
`harness.toolloop.run_prompt` の層別分岐が、同じ表と同じ関数を引いていることを
突き合わせる。畳んだ意味は「片方だけ直る」が起きなくなることなので、そこを縛る。
"""
from __future__ import annotations

import unittest

from agentcore import ollama_skills, ollama_tui, slashroute
from agentcore.harness import toolloop


class ParseTests(unittest.TestCase):
    def test_name_and_args(self):
        self.assertEqual(slashroute.parse_line("/verify docs/spec.md"),
                         ("verify", "docs/spec.md"))
        self.assertEqual(slashroute.parse_line("/verify"), ("verify", ""))

    def test_not_a_command_line(self):
        for line in ("verify", "/usr/bin/env", "/Verify", "/", "/-bad", "本文 /verify"):
            self.assertIsNone(slashroute.parse_line(line), line)

    def test_casefold_folds_the_name_only(self):
        # 人が打つ面（TUI）は `/MODEL` も受ける。引数のモデル名は潰さない。
        self.assertEqual(slashroute.parse_line("/MODEL Gemma4:12B", casefold=True),
                         ("model", "Gemma4:12B"))

    def test_casefold_is_opt_in(self):
        # 本文の切り出しは厳密なまま（`/README.md` を呼び出しと誤認しない）。
        self.assertIsNone(slashroute.parse_line("/README.md"))


class SplitLeadingTests(unittest.TestCase):
    def test_leading_block_only(self):
        calls, body = slashroute.split_leading("/a\n/b 引数\n本文\n/c")
        self.assertEqual(calls, [("a", ""), ("b", "引数")])
        self.assertEqual(body, "本文\n/c")

    def test_blank_line_ends_the_block(self):
        calls, body = slashroute.split_leading("/a\n\n/b")
        self.assertEqual(calls, [("a", "")])
        self.assertEqual(body, "/b")

    def test_no_command_line_keeps_everything(self):
        calls, body = slashroute.split_leading("ただの本文")
        self.assertEqual(calls, [])
        self.assertEqual(body, "ただの本文")

    def test_all_command_lines_leave_empty_body(self):
        calls, body = slashroute.split_leading("/a\n/b")
        self.assertEqual(calls, [("a", ""), ("b", "")])
        self.assertEqual(body, "")

    def test_skills_module_delegates_here(self):
        # `ollama_skills.split_leading_slashes` は綴りとして残るだけで実装を持たない。
        for prompt in ("/a\n/b 引数\n本文", "本文だけ", "/a\n\n本文", ""):
            self.assertEqual(ollama_skills.split_leading_slashes(prompt),
                             slashroute.split_leading(prompt), prompt)


class TableTests(unittest.TestCase):
    def test_aliases_resolve_to_the_same_row(self):
        self.assertIs(slashroute.resolve("exit"), slashroute.resolve("quit"))

    def test_unknown_name_is_none(self):
        # 表に無い名前は None。呼び出し側がスキルとして解決する（種別 D）。
        self.assertIsNone(slashroute.resolve("wiki-use"))

    def test_takes_args_follows_the_hint(self):
        self.assertTrue(slashroute.resolve("model").takes_args)
        self.assertFalse(slashroute.resolve("status").takes_args)

    def test_spellings_include_aliases_but_help_does_not(self):
        self.assertIn("/exit", slashroute.spellings())
        self.assertNotIn("/exit", slashroute.render_help())
        self.assertIn("/quit", slashroute.render_help())

    def test_onoff_spellings(self):
        self.assertEqual(slashroute.onoff_spellings(), ("/tools", "/think"))


class TuiSharesTheTableTests(unittest.TestCase):
    """TUI の一覧・補完・判定が同じ表を引いていること（綴りを 2 度書かない）。"""

    def test_completion_candidates_come_from_the_table(self):
        self.assertEqual(ollama_tui._LOCAL_COMMANDS, slashroute.spellings())
        self.assertEqual(ollama_tui._ONOFF_COMMANDS, slashroute.onoff_spellings())

    def test_help_lists_every_session_command(self):
        for cmd in slashroute.commands(slashroute.KIND_SESSION):
            self.assertIn(cmd.spell, ollama_tui._HELP, cmd.name)


class NormalizeLineTests(unittest.TestCase):
    def test_both_spellings_normalize_the_same(self):
        # 設定ファイル（agent-loop の `slash:`）は `/` を剥がして持つ規約。
        self.assertEqual(slashroute.normalize_line("/report --lang ja"), "report --lang ja")
        self.assertEqual(slashroute.normalize_line(" report --lang ja "), "report --lang ja")


class ApplyToGoalTests(unittest.TestCase):
    def test_native_keeps_the_lines(self):
        goal, skills = slashroute.apply_to_goal(
            "本文", ["summarize-logs", "report --lang ja"], native=True)
        self.assertEqual(goal, "/summarize-logs\n/report --lang ja\n\n本文")
        self.assertEqual(skills, [])

    def test_native_prefix_is_declared(self):
        # codex のスキル起動記号は `$`。記号は定義が宣言する（`skill_command_prefix`）。
        goal, _ = slashroute.apply_to_goal("本文", ["compact"], native=True, prefix="$")
        self.assertEqual(goal, "$compact\n\n本文")

    def test_non_native_consumes_the_lines(self):
        goal, skills = slashroute.apply_to_goal(
            "本文", ["tech-harvester ニュースをまとめて"], native=False)
        self.assertEqual(skills, ["tech-harvester"])
        self.assertIn("`tech-harvester` スキルの手順に従って実行してください。", goal)
        self.assertIn("引数: ニュースをまとめて", goal)
        self.assertTrue(goal.endswith("本文"))

    def test_duplicate_names_are_declared_once(self):
        _goal, skills = slashroute.apply_to_goal("本文", ["a x", "a y"], native=False)
        self.assertEqual(skills, ["a"])

    def test_empty_and_blank_lines_leave_the_goal_alone(self):
        for lines in (None, [], ["", "  "]):
            self.assertEqual(slashroute.apply_to_goal("本文", lines, native=True),
                             ("本文", []))
            self.assertEqual(slashroute.apply_to_goal("本文", lines, native=False),
                             ("本文", []))

    def test_run_prompt_uses_this_function(self):
        # `run_prompt` に残るのは「ネイティブのスラッシュを持つか」の 1 判定だけ。
        self.assertIs(toolloop.slashroute, slashroute)


if __name__ == "__main__":
    unittest.main()
