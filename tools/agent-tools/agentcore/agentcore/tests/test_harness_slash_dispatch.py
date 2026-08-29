"""ランチャが argv を組む前にコマンド行を読むこと（`harness run` / `agent-loop run`）。

設計: docs/plans/2026-08-27-agent-herd-cloud-cli-parity-slash-dispatch-design.md §3.2・§3.6。

見るのは 3 つ。
①`/sm <名前>` の 1 語でステートマシンが起きること（以前は agent-loop の設定でしか
起動できなかった。§3.4 の表）、②`/edit` の宣言が編集適用エンジンを決めること
（**aider の名前が出るのは宣言 1 行だけ**。§3.6）、③知らない `/名前` が明示エラーで
止まり、逃げ道が効くこと。

どれも「argv を組む前に決まっている」ことが要点なので、LLM もエージェント CLI も
起こさずに、決めた結果だけを見る。
"""
from __future__ import annotations

import argparse
import io
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))

from agentcore import slashroute  # noqa: E402
from agentcore.harness import toolloop  # noqa: E402


class _Sandbox:
    """作業ディレクトリと宣言ホームを一時領域へ閉じる。"""

    def __enter__(self):
        self._tmp = tempfile.TemporaryDirectory()
        # 解決済みで持つ。`cmd_run` は作業ディレクトリを `.resolve()` してから渡すので、
        # 素の一時パスと比べると macOS（`/var` → `/private/var`）でだけ食い違う。
        self.dir = Path(self._tmp.name).resolve()
        self._prev = os.environ.get("AGENT_COMMANDS_DIR")
        self.commands = self.dir / "commands"
        self.commands.mkdir()
        os.environ["AGENT_COMMANDS_DIR"] = str(self.commands)
        slashroute.clear_cache()
        return self

    def declare(self, name: str, body: str) -> None:
        (self.commands / f"{name}.md").write_text(body, encoding="utf-8")
        slashroute.clear_cache()

    def __exit__(self, *_exc):
        if self._prev is None:
            os.environ.pop("AGENT_COMMANDS_DIR", None)
        else:
            os.environ["AGENT_COMMANDS_DIR"] = self._prev
        slashroute.clear_cache()
        self._tmp.cleanup()


def _args(prompt, **kwargs):
    base = {"prompt": [prompt], "acceptance": [], "judge": False,
            "agent_cli": None, "model": None, "dir": None}
    base.update(kwargs)
    return argparse.Namespace(**base)


class StateMachineDispatchTests(unittest.TestCase):
    def test_sm_with_a_file_name_runs_that_workflow(self):
        with _Sandbox() as box:
            (box.dir / "nightly.yaml").write_text("states: []\n", encoding="utf-8")
            with mock.patch.object(toolloop, "_tl_progress"), \
                    mock.patch("agentcore.harness.statemachine.cmd_statemachine") as run:
                toolloop.cmd_run(_args("/sm nightly.yaml"), box.dir)
        sm_args = run.call_args.args[0]
        self.assertEqual(sm_args.workflow, str(box.dir / "nightly.yaml"))
        self.assertIsNone(sm_args.entry)

    def test_sm_with_an_unknown_name_is_an_entry(self):
        """実在しない名前は entry として渡す（`cmd_run` の「実在するパスなら中身」と同じ流儀）。"""
        with _Sandbox() as box:
            with mock.patch.object(toolloop, "_tl_progress"), \
                    mock.patch("agentcore.harness.statemachine.cmd_statemachine") as run:
                returned = toolloop.cmd_run(_args("/sm nightly --param a=1"), box.dir)
        self.assertIsNone(returned)
        sm_args = run.call_args.args[0]
        self.assertIsNone(sm_args.workflow)
        self.assertEqual(sm_args.entry, "nightly")
        self.assertEqual(sm_args.param, ["a=1"])

    def test_sm_needs_a_name(self):
        with _Sandbox() as box:
            with self.assertRaises(toolloop.ToolLoopError):
                toolloop._tl_statemachine_args(_args("/sm"), "", box.dir)

    def test_sm_refuses_unknown_flags(self):
        with _Sandbox() as box:
            with self.assertRaises(toolloop.ToolLoopError) as ctx:
                toolloop._tl_statemachine_args(_args("/sm x"), "x --wat", box.dir)
        self.assertIn("--wat", str(ctx.exception))

    def test_the_pin_is_carried_through(self):
        with _Sandbox() as box:
            sm_args = toolloop._tl_statemachine_args(
                _args("/sm x", agent_cli="claude", model="opus"), "x", box.dir)
        self.assertEqual((sm_args.agent_cli, sm_args.model), ("claude", "opus"))


class EditDeclarationTests(unittest.TestCase):
    def test_the_declaration_names_the_edit_engine(self):
        """実行レベルに書くのは用途の 1 語。どの編集適用エンジンかは宣言が決める。"""
        with _Sandbox() as box:
            box.declare("edit", "---\nagent: aider\n---\n編集役です。\n")
            with mock.patch.object(toolloop, "_tl_progress"), \
                    mock.patch.object(toolloop, "run_prompt",
                                      return_value={"ok": True}), \
                    mock.patch.object(toolloop, "_tl_resolve_agent",
                                      return_value={"cli": "aider", "model": None,
                                                    "spec": {}}) as resolve:
                with self.assertRaises(SystemExit):
                    toolloop.cmd_run(_args("/edit README を直して"), box.dir)
        self.assertEqual(resolve.call_args.args[0], "aider")

    def test_an_explicit_pin_still_wins(self):
        with _Sandbox() as box:
            box.declare("edit", "---\nagent: aider\n---\n編集役です。\n")
            with mock.patch.object(toolloop, "_tl_progress"), \
                    mock.patch.object(toolloop, "run_prompt",
                                      return_value={"ok": True}), \
                    mock.patch.object(toolloop, "_tl_resolve_agent",
                                      return_value={"cli": "claude", "model": None,
                                                    "spec": {}}) as resolve:
                with self.assertRaises(SystemExit):
                    toolloop.cmd_run(_args("/edit 直して", agent_cli="claude"), box.dir)
        self.assertEqual(resolve.call_args.args[0], "claude")

    def test_the_command_line_is_consumed(self):
        with _Sandbox() as box:
            box.declare("edit", "---\nagent: aider\n---\n編集役です。\n")
            with mock.patch.object(toolloop, "_tl_progress"), \
                    mock.patch.object(toolloop, "run_prompt",
                                      return_value={"ok": True}) as run, \
                    mock.patch.object(toolloop, "_tl_resolve_agent",
                                      return_value={"cli": "aider", "model": None,
                                                    "spec": {}}):
                with self.assertRaises(SystemExit):
                    toolloop.cmd_run(_args("/edit README を直して"), box.dir)
        self.assertEqual(run.call_args.kwargs["goal"], "README を直して")


class UnknownCommandTests(unittest.TestCase):
    def test_an_unknown_leading_command_stops_before_the_agent(self):
        with _Sandbox() as box:
            with mock.patch.object(toolloop, "_tl_resolve_agent") as resolve:
                with self.assertRaises(SystemExit) as ctx:
                    toolloop.cmd_run(_args("/verfy 直して"), box.dir)
        self.assertEqual(ctx.exception.code, 2)
        resolve.assert_not_called()

    def test_a_leading_blank_line_sends_it_as_body(self):
        with _Sandbox() as box:
            with mock.patch.object(toolloop, "_tl_progress"), \
                    mock.patch.object(toolloop, "run_prompt",
                                      return_value={"ok": True}) as run, \
                    mock.patch.object(toolloop, "_tl_resolve_agent",
                                      return_value={"cli": "aider", "model": None,
                                                    "spec": {}}):
                with self.assertRaises(SystemExit):
                    toolloop.cmd_run(_args("\n/tmp を消して"), box.dir)
        # 空行はそのまま残る（本文の一部）。要点はコマンドとして解釈されないこと。
        self.assertEqual(run.call_args.kwargs["goal"], "\n/tmp を消して")

    def test_a_command_line_with_no_body_is_refused(self):
        with _Sandbox() as box:
            with self.assertRaises(SystemExit) as ctx:
                toolloop.cmd_run(_args("/ask"), box.dir)
        self.assertEqual(ctx.exception.code, 2)

    def test_a_session_command_is_refused_in_a_one_shot_run(self):
        with _Sandbox() as box:
            with self.assertRaises(SystemExit) as ctx:
                toolloop.cmd_run(_args("/help\n本文"), box.dir)
        self.assertEqual(ctx.exception.code, 2)


if __name__ == "__main__":
    unittest.main()


class ShapeIsNotAPurposeTests(unittest.TestCase):
    """実行形（種別 B）の名前を用途（種別 C）の口へ渡さない（未決 2 の決着・2026-08-29）。

    `statemachine` はハーネスであって用途ではない。用途別順位表に無い名前は必ず共通
    candidates へ落ちるので、渡しても挙動は変わらず「用途を渡したのに効かない」という嘘が
    1 つ増えるだけだった。ルータが 2 軸を分けている以上、口も分ける。
    """

    def test_the_statemachine_asks_for_a_decision_without_a_purpose(self):
        from agentcore.harness import statemachine as sm
        seen = []
        with _Sandbox() as box:
            (box.dir / "nightly.yaml").write_text("states: []\n", encoding="utf-8")
            args = argparse.Namespace(
                workflow=str(box.dir / "nightly.yaml"), entry=None, config=None,
                param=[], input=None, dir=str(box.dir), agent_cli=None, model=None)
            with mock.patch.object(sm, "_control_policy_decision",
                                   side_effect=lambda *a, **k: seen.append((a, k))), \
                    mock.patch.object(sm, "_sm_progress"), \
                    mock.patch.object(sm, "run_statemachine",
                                      return_value={"ok": True, "finalState": "complete"}), \
                    mock.patch("sys.stdout", io.StringIO()), \
                    self.assertRaises(SystemExit):
                sm.cmd_statemachine(args, box.dir)
        self.assertEqual(seen, [((), {})],
                         "用途は渡さない（statemachine は実行形であって用途ではない）")

    def test_the_catalog_does_not_claim_the_harness_as_a_purpose(self):
        """管理面のカタログ側にも載せない（載せると同じ取り違えが逆から入る）。"""
        catalog = (Path(__file__).resolve().parents[5] / "tools" / "agent-dashboard" / "src"
                   / "features" / "orchestration" / "main" / "purpose-operations.js")
        self.assertNotIn("statemachine:", catalog.read_text(encoding="utf-8"))
