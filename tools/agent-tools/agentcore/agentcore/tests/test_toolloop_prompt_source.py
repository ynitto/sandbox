"""`harness run` のプロンプト引数（ファイルパスか本文か）の見分け。

`cmd_run` は「実在するファイルを渡したら中身を本文にする」ために、受け取った文字列を
そのまま `Path.is_file()` へ通していた。数 KB の自然言語プロンプトはファイル名として
長すぎるので、Linux では ENAMETOOLONG、Windows では「ファイル名が正しくない」等の
OSError がそこで漏れ、実行が始まる前に落ちる。`/sm <名前>` の名前解決も同じ形だった。
herdcli の JSON 引数で直したのと同じ種類の障害（test_herdcli_json_source.py）。

契約:
- パスとして実在するファイルなら、その本文を読む
- パスとして見られない（stat が OSError / ValueError）なら「ファイルではない」
- ただし実在するファイルを見つけたあとの読み込み失敗は、黙って本文扱いにせず明示エラー

OS の差に依存しないよう、長さはどの OS のファイル名上限（255 前後）も超える 4KB 超に
し、stat の失敗そのものは mock で起こす試験も置く。

    python3 -m pytest tools/agent-tools/agentcore/agentcore/tests/test_toolloop_prompt_source.py
"""
from __future__ import annotations

import argparse
import errno
import io
import os
import sys
import tempfile
import unittest
from contextlib import redirect_stderr
from pathlib import Path
from unittest import mock

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))

from agentcore import slashroute  # noqa: E402
from agentcore.harness import toolloop  # noqa: E402

LONG_PROMPT = ("次の仕様に沿って README を直してください。" * 400).strip()


def _args(prompt, **kwargs):
    base = {"prompt": [prompt], "acceptance": [], "judge": False,
            "agent_cli": None, "model": None, "dir": None}
    base.update(kwargs)
    return argparse.Namespace(**base)


class _Box:
    def __enter__(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.dir = Path(self._tmp.name).resolve()
        self._prev = os.environ.get("AGENT_COMMANDS_DIR")
        (self.dir / "commands").mkdir()
        os.environ["AGENT_COMMANDS_DIR"] = str(self.dir / "commands")
        slashroute.clear_cache()
        return self

    def __exit__(self, *_exc):
        if self._prev is None:
            os.environ.pop("AGENT_COMMANDS_DIR", None)
        else:
            os.environ["AGENT_COMMANDS_DIR"] = self._prev
        slashroute.clear_cache()
        self._tmp.cleanup()


def _run_goal(box, prompt):
    """cmd_run が実行へ渡す本文（run_prompt の第 1 引数）を返す。"""
    with mock.patch.object(toolloop, "_tl_progress"), \
            mock.patch.object(toolloop, "run_prompt", return_value={"ok": True}) as run, \
            mock.patch.object(toolloop, "_tl_resolve_agent",
                              return_value={"cli": "claude", "model": None, "spec": {}}):
        with redirect_stderr(io.StringIO()), mock.patch("sys.stdout", io.StringIO()):
            try:
                toolloop.cmd_run(_args(prompt), box.dir)
            except SystemExit:
                pass
    return run.call_args.kwargs["goal"] if run.call_args else None


class PromptSourceTests(unittest.TestCase):
    def test_long_natural_language_prompt_is_kept(self):
        self.assertGreater(len(LONG_PROMPT.encode("utf-8")), 4096)
        with _Box() as box:
            goal = _run_goal(box, LONG_PROMPT)
        self.assertIsNotNone(goal)
        self.assertIn(LONG_PROMPT, goal)

    def test_existing_prompt_file_is_read(self):
        with _Box() as box:
            (box.dir / "task.md").write_text("ファイルの依頼です\n", encoding="utf-8")
            goal = _run_goal(box, "task.md")
        self.assertIn("ファイルの依頼です", goal)
        self.assertNotIn("task.md", goal)

    def test_missing_short_name_is_the_prompt(self):
        with _Box() as box:
            goal = _run_goal(box, "README を直して")
        self.assertIn("README を直して", goal)

    def test_stat_failure_means_not_a_file(self):
        """OS ごとの失敗の形（ENAMETOOLONG・Windows の無効な名前）を同じ意味に揃える。"""
        for exc in (OSError(errno.ENAMETOOLONG, "File name too long"),
                    OSError(22, "The filename, directory name, or volume label syntax "
                                "is incorrect"),
                    ValueError("embedded null byte")):
            with self.subTest(exc=exc), _Box() as box:
                with mock.patch.object(Path, "is_file", side_effect=exc):
                    self.assertEqual(
                        toolloop._tl_prompt_source("短い依頼", box.dir), "短い依頼")

    def test_unreadable_existing_file_is_an_explicit_error(self):
        """実在するファイルの読み込み失敗は本文扱いに落とさない。"""
        with _Box() as box:
            path = box.dir / "task.md"
            path.write_bytes(b"\xff\xfe\x00broken")       # UTF-8 として読めない
            with self.assertRaises(toolloop.ToolLoopError) as ctx:
                toolloop._tl_prompt_source("task.md", box.dir)
            self.assertIn("task.md", str(ctx.exception))
            err = io.StringIO()
            with mock.patch.object(toolloop, "run_prompt") as run, redirect_stderr(err):
                with self.assertRaises(SystemExit) as code:
                    toolloop.cmd_run(_args("task.md"), box.dir)
            run.assert_not_called()
            self.assertNotEqual(code.exception.code, 0)
            self.assertIn("task.md", err.getvalue())


class StateMachineNameTests(unittest.TestCase):
    def test_long_sm_name_is_an_entry_not_an_oserror(self):
        name = "x" * 5000
        with _Box() as box:
            sm_args = toolloop._tl_statemachine_args(_args("/sm"), name, box.dir)
        self.assertIsNone(sm_args.workflow)
        self.assertEqual(sm_args.entry, name)

    def test_long_sm_name_through_cmd_run_does_not_leak_oserror(self):
        with _Box() as box:
            with mock.patch.object(toolloop, "_tl_progress"), \
                    mock.patch("agentcore.harness.statemachine.cmd_statemachine") as run:
                toolloop.cmd_run(_args("/sm " + "y" * 5000), box.dir)
        self.assertEqual(run.call_args.args[0].entry, "y" * 5000)

    def test_existing_workflow_file_still_wins(self):
        with _Box() as box:
            (box.dir / "nightly.yaml").write_text("states: []\n", encoding="utf-8")
            sm_args = toolloop._tl_statemachine_args(_args("/sm"), "nightly.yaml", box.dir)
        self.assertEqual(sm_args.workflow, str(box.dir / "nightly.yaml"))


if __name__ == "__main__":
    unittest.main()
