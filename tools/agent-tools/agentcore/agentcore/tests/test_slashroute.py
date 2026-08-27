"""スラッシュ行のルータ（`agentcore.slashroute`）。

設計: docs/plans/2026-08-27-agent-herd-cloud-cli-parity-slash-dispatch-design.md §3.2。

見るのは 2 つ。①切り出しと適用の規約そのもの、②**3 か所に散っていた解釈がここへ
畳まれたこと**——`ollama_skills` の切り出し・`ollama_tui` のローカルコマンド表・
`harness.toolloop.run_prompt` の層別分岐が、同じ表と同じ関数を引いていることを
突き合わせる。畳んだ意味は「片方だけ直る」が起きなくなることなので、そこを縛る。
"""
from __future__ import annotations

import types
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
        self.assertIs(slashroute.lookup("exit"), slashroute.lookup("quit"))

    def test_unknown_name_is_none(self):
        # 表に無い名前は None。呼び出し側がスキルとして解決する（種別 D）。
        self.assertIsNone(slashroute.lookup("wiki-use"))

    def test_takes_args_follows_the_hint(self):
        self.assertTrue(slashroute.lookup("model").takes_args)
        self.assertFalse(slashroute.lookup("status").takes_args)

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


class _StubAgentcli:
    """`variants` の申告だけを持つ最小の定義ローダ。"""

    def __init__(self, variants: dict) -> None:
        self.variants = variants
        self.calls: "list[tuple]" = []

    def resolve_variant(self, name, purpose, project_dir=None):
        self.calls.append((name, purpose, project_dir))
        return self.variants.get(purpose)


class ResolvePurposeTests(unittest.TestCase):
    """用途 → 起動形の調停（設計 §3.3 / G2・G4）。"""

    def _cli(self, **variants):
        return _StubAgentcli({k: {"agent_cli": v[0], "default_model": v[1]}
                              for k, v in variants.items()})

    def test_declared_variant_switches_the_launch_form(self):
        mod = self._cli(judge=("ollama-json", "gemma4:e4b"))
        self.assertEqual(
            slashroute.resolve(command="judge", cli="ollama", agentcli=mod),
            {"agent_cli": "ollama-json", "model": "gemma4:e4b", "variant": True})

    def test_no_declaration_leaves_everything_alone(self):
        mod = self._cli(judge=("ollama-json", "gemma4:e4b"))
        self.assertEqual(
            slashroute.resolve(command="distill", cli="ollama", model="m", agentcli=mod),
            {"agent_cli": "ollama", "model": "m", "variant": False})

    def test_there_is_no_allow_list(self):
        """engine ごとの許可リストは無い——申告が唯一の許可リストである（G2）。

        以前は flow / project / audit が各々の集合を持ち、`ollama.json` が 15 キーを
        宣言しても flow は 9・project は 6・audit は 2 しか引かなかった。
        """
        mod = self._cli(**{"repo_map": ("x-json", "")})
        routed = slashroute.resolve(command="repo_map", cli="x", agentcli=mod)
        self.assertEqual(routed["agent_cli"], "x-json")
        self.assertEqual(mod.calls, [("x", "repo_map", None)])

    def test_explicit_model_is_not_overridden(self):
        mod = self._cli(verify=("ollama-verify", "gemma4:12b"))
        routed = slashroute.resolve(command="verify", cli="ollama", model="qwen3:8b",
                                    explicit_model=True, agentcli=mod)
        self.assertEqual(routed, {"agent_cli": "ollama-verify", "model": "qwen3:8b",
                                  "variant": True})

    def test_by_purpose_decision_is_not_overridden(self):
        """用途別順位表の決定はその用途の実測。変種の既定で上書きしない（G4）。"""
        mod = self._cli(judge=("ollama-json", "gemma4:e4b"))
        routed = slashroute.resolve(command="judge", cli="ollama", model="gemma4:12b",
                                    by_purpose=True, agentcli=mod)
        self.assertEqual(routed, {"agent_cli": "ollama-json", "model": "gemma4:12b",
                                  "variant": True})

    def test_flat_candidates_still_defer_to_the_variant_default(self):
        # 用途を知らない共通の順位表由来のモデルは、変種の用途専用チューニングに譲る。
        mod = self._cli(verify=("ollama-verify", "gemma4:12b"))
        routed = slashroute.resolve(command="verify", cli="ollama", model="gemma4:e4b",
                                    agentcli=mod)
        self.assertEqual(routed["model"], "gemma4:12b")

    def test_session_command_names_are_not_purposes(self):
        """名前空間は 1 つ。`/model` が用途としても解釈される状態を作らない。"""
        mod = self._cli(model=("x-json", "m"))
        routed = slashroute.resolve(command="model", cli="x", agentcli=mod)
        self.assertEqual(routed["agent_cli"], "x")
        self.assertEqual(mod.calls, [])

    def test_broken_declaration_does_not_kill_the_run(self):
        mod = types.SimpleNamespace(
            resolve_variant=lambda *a, **k: (_ for _ in ()).throw(RuntimeError("壊れた定義")))
        self.assertEqual(
            slashroute.resolve(command="verify", cli="ollama", model="m", agentcli=mod),
            {"agent_cli": "ollama", "model": "m", "variant": False})

    def test_empty_command_is_a_no_op(self):
        mod = self._cli(verify=("ollama-verify", "gemma4:12b"))
        self.assertEqual(slashroute.resolve(command="", cli="ollama", agentcli=mod),
                         {"agent_cli": "ollama", "model": None, "variant": False})
        self.assertEqual(mod.calls, [])

    def test_bundled_definitions_declare_the_whole_former_allow_list(self):
        """同梱定義の申告が、消した 3 つの許可リストの和集合を覆っていること。

        削除が「宣言の効く範囲を狭めた」ではなく「engine の重複を消した」であることの
        確認。ここが割れたら、どの用途が振り替わらなくなったのかが分かる。
        """
        from agentcore import agentcli
        former = {
            # flow: JSON_CONTRACT_ROLES | LIST_CONTRACT_ROLES | {retrieve, verify}
            "planner", "evaluator", "split", "filter", "judge", "reduce", "extract",
            "retrieve", "verify",
            # project: JSON_CONTRACT_PURPOSES
            "plan", "review", "prioritize", "route", "adjudicate", "assess",
        }
        declared = set(agentcli.load_cli("ollama").get("variants") or {})
        self.assertEqual(former - declared, set())


if __name__ == "__main__":
    unittest.main()
