"""agent-loop entry の `statemachine:` / `input:` の読み取りと、`--entry` 実行の回帰。

宣言の解釈はここ 1 実装（agentcore.loopentry）に閉じ、デーモン・agent-herd・
dashboard がそれを引く。写しが増えると「入口によって条件が違う」が起きるので、
規則そのものをここで縛る。

仕様: docs/specs/agent-loop-spec.md §2.3 / §3.5。
"""
from __future__ import annotations

import argparse
import io
import json
import pathlib
import sys
import tempfile
import unittest
from contextlib import redirect_stdout
from unittest import mock

from agentcore import herdcli, loopentry
from agentcore.harness import statemachine as sm


class WorkflowReferenceTests(unittest.TestCase):
    """`statemachine:` の値 → 作業ディレクトリからの相対パス。"""

    def test_a_bare_name_expands_to_the_conventional_location(self):
        self.assertEqual(loopentry.workflow_reference("digest"),
                         ".statemachine/digest/workflow.yaml")

    def test_a_directory_path_gets_the_workflow_file_appended(self):
        self.assertEqual(loopentry.workflow_reference(".statemachine/digest"),
                         ".statemachine/digest/workflow.yaml")

    def test_a_file_path_is_kept_as_written(self):
        self.assertEqual(loopentry.workflow_reference("flows/digest/machine.yml"),
                         "flows/digest/machine.yml")

    def test_normalizing_is_idempotent(self):
        once = loopentry.workflow_reference("digest")
        self.assertEqual(loopentry.workflow_reference(once), once)

    def test_paths_outside_the_workspace_are_refused_at_read_time(self):
        # ハーネスも作業フォルダの外は読まないが、設定を読んだ時点で断るほうが直しやすい。
        for value in ("../elsewhere", "/etc/passwd", "~/digest", "", "..", "C:/x/y.yaml"):
            with self.subTest(value=value):
                with self.assertRaises(loopentry.LoopEntryError):
                    loopentry.workflow_reference(value)

    def test_the_display_name_comes_from_the_conventional_location(self):
        self.assertEqual(
            loopentry.workflow_display_name(".statemachine/digest/workflow.yaml"), "digest")
        self.assertEqual(loopentry.workflow_display_name("flows/x/machine.yml"), "")


class StatemachineSpecTests(unittest.TestCase):
    """実行条件（`input:` のマップと、自由文としての `prompt`）の組み立て。"""

    def test_no_declaration_is_not_a_statemachine_entry(self):
        self.assertIsNone(loopentry.statemachine_spec({"name": "n", "prompt": "p"}))

    def test_the_input_map_becomes_the_parameters(self):
        spec = loopentry.statemachine_spec(
            {"statemachine": "digest", "input": {"topic": "llm", "count": 3, "dry": True}})
        self.assertEqual(spec["parameters"],
                         {"topic": "llm", "count": "3", "dry": "true"})
        self.assertFalse(spec["prompt_is_input"])

    def test_the_prompt_lands_in_the_single_free_text_slot(self):
        spec = loopentry.statemachine_spec(
            {"statemachine": "digest", "prompt": "  今日の要約を書いて  "})
        self.assertEqual(spec["parameters"], {"input": "今日の要約を書いて"})
        self.assertTrue(spec["prompt_is_input"])

    def test_the_two_forms_compose(self):
        spec = loopentry.statemachine_spec(
            {"statemachine": "digest", "input": {"topic": "llm"}, "prompt": "本文"})
        self.assertEqual(spec["parameters"], {"topic": "llm", "input": "本文"})
        self.assertEqual(spec["input"], {"topic": "llm"},
                         "宣言そのままの写しは自由文を混ぜない（再正規化できる形で残す）")

    def test_two_declarations_of_the_same_slot_fail_instead_of_one_winning(self):
        with self.assertRaises(loopentry.LoopEntryError):
            loopentry.statemachine_spec(
                {"statemachine": "digest", "input": {"input": "A"}, "prompt": "B"})

    def test_a_dispatched_prompt_replaces_the_declared_one(self):
        # フックが本文を決めた実行。entry の prompt ではなく届いた本文が条件になる。
        spec = loopentry.statemachine_spec(
            {"statemachine": "digest", "prompt": "既定"}, prompt="フックの本文")
        self.assertEqual(spec["parameters"], {"input": "フックの本文"})

    def test_nested_and_empty_values_are_refused(self):
        for bad in ({"topic": {"a": 1}}, {"topic": ["a"]}, {"topic": None}, {"": "x"}):
            with self.subTest(bad=bad):
                with self.assertRaises(loopentry.LoopEntryError):
                    loopentry.statemachine_spec({"statemachine": "digest", "input": bad})

    def test_a_non_string_declaration_is_refused(self):
        with self.assertRaises(loopentry.LoopEntryError):
            loopentry.statemachine_spec({"statemachine": {"name": "digest"}})


def _workspace(entries, *, name="agent-loop.yaml", subdir=".agents"):
    root = pathlib.Path(tempfile.mkdtemp())
    directory = root / subdir if subdir else root
    directory.mkdir(parents=True, exist_ok=True)
    body = json.dumps({"prompts": entries}, ensure_ascii=False)
    (directory / name).write_text(body, encoding="utf-8")
    (root / ".statemachine" / "digest").mkdir(parents=True, exist_ok=True)
    (root / ".statemachine" / "digest" / "workflow.yaml").write_text("states: {}\n",
                                                                    encoding="utf-8")
    return root


class ResolveEntryTests(unittest.TestCase):
    """`--entry` が設定ファイルから引くもの。"""

    def test_it_resolves_the_workflow_and_the_conditions(self):
        root = _workspace([{"name": "日次", "statemachine": "digest",
                            "input": {"topic": "llm"}, "prompt": "本文",
                            "agent_cli": "ollama", "model": "gemma4:e4b"}],
                          name="agent-loop.json")
        plan = loopentry.resolve_entry("日次", cwd=str(root))
        self.assertEqual(plan["workflow"], ".statemachine/digest/workflow.yaml")
        self.assertEqual(plan["parameters"], {"topic": "llm", "input": "本文"})
        self.assertEqual(plan["agent_cli"], "ollama")
        self.assertEqual(plan["model"], "gemma4:e4b")

    def test_an_entry_without_a_declaration_is_refused_with_a_pointer(self):
        root = _workspace([{"name": "ただの定期", "prompt": "本文"}], name="agent-loop.json")
        with self.assertRaises(loopentry.LoopEntryError) as ctx:
            loopentry.resolve_entry("ただの定期", cwd=str(root))
        self.assertIn("--workflow", str(ctx.exception))

    def test_an_unknown_name_lists_what_exists(self):
        root = _workspace([{"name": "日次", "statemachine": "digest"}], name="agent-loop.json")
        with self.assertRaises(loopentry.LoopEntryError) as ctx:
            loopentry.resolve_entry("週次", cwd=str(root))
        self.assertIn("日次", str(ctx.exception))

    def test_a_missing_config_names_where_it_looked(self):
        with tempfile.TemporaryDirectory() as empty:
            # ホームの設定を拾わせない（結果が実行環境の持ち物で変わる）。
            with mock.patch.object(pathlib.Path, "home", staticmethod(
                    lambda: pathlib.Path(empty))):
                with self.assertRaises(loopentry.LoopEntryError) as ctx:
                    loopentry.resolve_entry("日次", cwd=empty)
        self.assertIn("agent-loop.yaml", str(ctx.exception))


class CmdStatemachineEntryTests(unittest.TestCase):
    """`agent-loop statemachine --entry` / `agent-herd harness statemachine --entry`。"""

    def _args(self, **extra):
        base = dict(entry=None, workflow=None, config=None, dir=None, param=[],
                    input=None, agent_cli=None, model=None)
        base.update(extra)
        return argparse.Namespace(**base)

    def _run(self, args, cwd):
        seen = {}

        def fake_run(**kwargs):
            seen.update(kwargs)
            return {"ok": True, "finalState": "done", "logFile": "x", "files": []}

        with mock.patch.object(sm, "run_statemachine", fake_run), \
             mock.patch.object(sm, "_sm_resolve_agent",
                               lambda cli, model, work: {"cli": cli, "model": model}), \
             redirect_stdout(io.StringIO()):
            with self.assertRaises(SystemExit) as exit_ctx:
                sm.cmd_statemachine(args, pathlib.Path(cwd))
        return seen, exit_ctx.exception.code

    def test_the_entry_supplies_the_workflow_and_the_conditions(self):
        root = _workspace([{"name": "日次", "statemachine": "digest",
                            "input": {"topic": "llm"}, "prompt": "本文"}],
                          name="agent-loop.json")
        seen, code = self._run(self._args(entry="日次"), str(root))
        self.assertEqual(code, 0)
        self.assertEqual(seen["workflow_path"], ".statemachine/digest/workflow.yaml")
        self.assertEqual(seen["parameters"], {"topic": "llm", "input": "本文"})

    def test_typed_parameters_win_over_the_declared_ones(self):
        root = _workspace([{"name": "日次", "statemachine": "digest",
                            "input": {"topic": "llm"}, "prompt": "本文"}],
                          name="agent-loop.json")
        seen, _ = self._run(
            self._args(entry="日次", param=["topic=rust"], input="今日ぶん"), str(root))
        self.assertEqual(seen["parameters"], {"topic": "rust", "input": "今日ぶん"})

    def test_the_entry_agent_is_used_unless_the_flag_pins_one(self):
        root = _workspace([{"name": "日次", "statemachine": "digest",
                            "agent_cli": "ollama", "model": "gemma4:e4b"}],
                          name="agent-loop.json")
        seen, _ = self._run(self._args(entry="日次"), str(root))
        self.assertEqual(seen["agent"], {"cli": "ollama", "model": "gemma4:e4b"})
        seen, _ = self._run(self._args(entry="日次", agent_cli="aider"), str(root))
        self.assertEqual(seen["agent"]["cli"], "aider")

    def test_the_entry_cwd_becomes_the_working_directory(self):
        root = _workspace([{"name": "日次", "statemachine": "digest"}],
                          name="agent-loop.json")
        entries = json.loads((root / ".agents" / "agent-loop.json").read_text(encoding="utf-8"))
        entries["prompts"][0]["cwd"] = str(root)
        (root / ".agents" / "agent-loop.json").write_text(json.dumps(entries), encoding="utf-8")
        with tempfile.TemporaryDirectory() as elsewhere:
            # --config を明示するので探索は走らないが、cwd は別の場所にしておく
            # （entry の cwd が勝つことを見るテストなので、両者を必ず食い違わせる）。
            seen, _ = self._run(self._args(entry="日次", config=str(
                root / ".agents" / "agent-loop.json")), elsewhere)
        self.assertEqual(seen["cwd"], str(root.resolve()))

    def test_workflow_and_entry_together_are_refused(self):
        root = _workspace([{"name": "日次", "statemachine": "digest"}],
                          name="agent-loop.json")
        args = self._args(entry="日次", workflow="other.yaml")
        with redirect_stdout(io.StringIO()), \
             mock.patch.object(sys, "stderr", io.StringIO()):
            with self.assertRaises(SystemExit) as ctx:
                sm.cmd_statemachine(args, pathlib.Path(root))
        self.assertEqual(ctx.exception.code, 1)


class HerdEntryFlagTests(unittest.TestCase):
    """agent-herd 側の綴り。agent-loop と同じ名前で受ける。"""

    def test_the_entry_flag_reaches_the_harness(self):
        seen = []
        rc = herdcli.cmd_harness(
            ["statemachine", "--entry", "日次", "--config", "cfg.yaml"],
            runner=lambda kind, args, cwd: seen.append(args) or 0)
        self.assertEqual(rc, 0)
        self.assertEqual(seen[0].entry, "日次")
        self.assertEqual(seen[0].config, "cfg.yaml")
        self.assertIsNone(seen[0].agent_cli,
                          "打たなかった --agent-cli は None（entry の宣言に道を譲る）")

    def test_neither_or_both_is_refused_before_the_harness_starts(self):
        for tokens in (["statemachine"],
                       ["statemachine", "--workflow", "w.yaml", "--entry", "日次"]):
            with self.subTest(tokens=tokens):
                err = io.StringIO()
                rc = herdcli.cmd_harness(tokens, err=err, runner=lambda *a: 0)
                self.assertEqual(rc, 2)

    def test_the_help_mentions_the_entry_form(self):
        self.assertIn("--entry", herdcli.HARNESS_HELP)


if __name__ == "__main__":
    unittest.main()
