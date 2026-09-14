"""複数行の `command:` は 1 つのシェルへ一度に渡す。

行ごとに別のプロセスで起こすと、`cd` も変数も次の行へ残らない——「1 つの手順を上から
書いた」つもりの宣言が、実際には別々の実行になっていた。複数行はスクリプトとして扱い、
同じプロセスが最後まで読む。段ごとに上限や許容を変えたいものは、こちらではなく
`commands:` の列（`test_command_sequence.py`）で書く。

1 行の文字列は従来どおり argv の直接実行（シェル記号は宣言の時点で断る）。

仕様: docs/specs/agent-loop-spec.md §2.3.2。
"""
import sys
import tempfile
import time
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import agent_loop as al  # noqa: E402


class ShellCommandTest(unittest.TestCase):

    def test_state_carries_from_one_line_to_the_next(self):
        with tempfile.TemporaryDirectory() as td:
            (Path(td) / "build").mkdir()
            spec = al._loopentry.command_spec({"command": {
                "argv": "cd build\nexport MARK=here\npwd\necho $MARK\n", "timeout_sec": 30}})
            result = al._commandrun.run_command(spec, cwd=td)
            self.assertTrue(result["ok"])
            self.assertEqual(result["stdout"].splitlines()[-2:],
                             [str(Path(td).resolve() / "build"), "here"])

    def test_blank_lines_and_crlf_do_not_break_the_script(self):
        with tempfile.TemporaryDirectory() as td:
            spec = al._loopentry.command_spec({"command": "echo one\r\n\r\necho two\r\n"})
            result = al._commandrun.run_command(spec, cwd=td)
            self.assertTrue(result["ok"])
            self.assertEqual(result["stdout"], "one\ntwo\n")

    def test_the_script_stops_at_the_first_failing_line(self):
        with tempfile.TemporaryDirectory() as td:
            spec = al._loopentry.command_spec({"command": "echo one\nfalse\ntouch must-not-exist"})
            result = al._commandrun.run_command(spec, cwd=td)
            self.assertFalse(result["ok"])
            self.assertEqual(result["status"], 1)
            self.assertEqual(result["stopReason"], "command_exit")
            self.assertFalse((Path(td) / "must-not-exist").exists())

    def test_a_line_can_declare_that_it_tolerates_a_failure(self):
        with tempfile.TemporaryDirectory() as td:
            spec = al._loopentry.command_spec({"command": "false || true\necho reached"})
            result = al._commandrun.run_command(spec, cwd=td)
            self.assertTrue(result["ok"])
            self.assertEqual(result["stdout"], "reached\n")

    def test_shell_symbols_are_allowed_and_a_broken_pipe_still_fails(self):
        with tempfile.TemporaryDirectory() as td:
            spec = al._loopentry.command_spec({"command": {"shell": "false | cat", "timeout_sec": 30}})
            self.assertEqual(spec["shell"], "false | cat")
            self.assertFalse(al._commandrun.run_command(spec, cwd=td)["ok"])

    def test_one_line_stays_an_argv_execution(self):
        # 1 行はこれまでどおり。シェルへ回すと、書けても効かない記号が黙って通ってしまう。
        spec = al._loopentry.command_spec({"command": "echo hi"})
        self.assertNotIn("shell", spec)
        self.assertEqual(spec["argv"], ["echo", "hi"])
        with self.assertRaisesRegex(al._loopentry.LoopEntryError, "シェル記号"):
            al._loopentry.command_spec({"command": "a | b"})

    def test_the_timeout_covers_the_whole_script_and_takes_the_children_with_it(self):
        with tempfile.TemporaryDirectory() as td:
            spec = al._loopentry.command_spec({"command": {
                "shell": "(sleep 2; touch grandchild) &\nsleep 30\n", "timeout_sec": 1}})
            result = al._commandrun.run_command(spec, cwd=td)
            self.assertFalse(result["ok"])
            self.assertEqual(result["stopReason"], "command_timeout")
            self.assertLess(result["durationSec"], 10)
            time.sleep(2.5)
            self.assertFalse((Path(td) / "grandchild").exists())

    def test_material_is_never_substituted_into_a_script(self):
        # 値は材料であってコマンドではない。シェルへ差し込むと記号が実行になる。
        spec = al._loopentry.command_spec({"command": "echo {title}\necho done"})
        rendered = al._loopentry.render_command(spec, {"title": "; rm -rf ~"})
        self.assertEqual(rendered["argv"], spec["argv"])
        self.assertIn("{title}", rendered["shell"])

    def test_a_shell_entry_cannot_take_material_from_a_hook(self):
        with self.assertRaisesRegex(ValueError, "併用できません"):
            al.validate_entries([{"name": "c", "command": "echo a\necho b",
                                  "hooks": "some-hook.py", "interval_minutes": 5}])
        # argv の形なら従来どおり書ける（値は 1 字句のまま渡る）。
        entries = al.validate_entries([{"name": "c", "command": ["s.py", "{iid}"],
                                        "hooks": "some-hook.py", "interval_minutes": 5}])
        self.assertEqual(entries[0]["command"]["argv"][1], "{iid}")

    def test_an_empty_script_is_refused(self):
        for bad in ({"shell": "   "}, {"shell": ["a"]}):
            with self.subTest(bad=bad):
                with self.assertRaises(al._loopentry.LoopEntryError):
                    al._loopentry.command_spec({"command": bad})


if __name__ == "__main__":
    unittest.main()
