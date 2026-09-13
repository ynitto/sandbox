"""`command:` のコマンドの列——上から順に、1 段ずつ別のプロセスで実行する。

複数行の文字列は 1 つのシェルへ渡す別の形（`test_command_shell.py`）。段ごとに上限や
許す終了コードを書き分けたいものは、こちらの列で書く。

仕様: docs/specs/agent-loop-spec.md §2.3.2。
"""
import sys
import tempfile
import unittest
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import agent_loop as al


class CommandListTest(unittest.TestCase):
    """`commands:` に並べたコマンドの列と、段ごとの宣言。"""

    def _exit(self, root, name, status):
        (root / name).write_text(f"import sys\nsys.exit({status})\n")
        return f'"{sys.executable}" {root / name}'

    def test_steps_run_in_order_and_stop_at_the_first_failure(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            (root / 'first.py').write_text("from pathlib import Path\nPath('first').touch()\n")
            (root / 'later.py').write_text("from pathlib import Path\nPath('later').touch()\n")
            spec = al._loopentry.command_spec({'command': {'commands': [
                f'"{sys.executable}" first.py', self._exit(root, 'fail.py', 3),
                f'"{sys.executable}" later.py'], 'timeout_sec': 30}})
            result = al._commandrun.run_command(spec, cwd=td)
            self.assertFalse(result['ok'])
            self.assertEqual(result['completedCommands'], 1)
            self.assertTrue((root / 'first').exists())
            self.assertFalse((root / 'later').exists())

    def test_a_step_only_overrides_what_it_declares(self):
        # 6 段のうち 2 段だけ 1 を許す、が書けること（使用量較正の移行に要る）。
        spec = al._loopentry.command_spec({'command': {
            'commands': ['agent-audit collect',
                         {'argv': 'agent-audit extract', 'allow_status': [0, 1]},
                         {'argv': 'agent-audit tune --apply', 'timeout_sec': 900}],
            'timeout_sec': 600, 'env': {'SCOPE': 'home'}}})
        self.assertEqual([step['allow_status'] for step in spec['commands']],
                         [[0], [0, 1], [0]])
        self.assertEqual([step['timeout_sec'] for step in spec['commands']], [600, 600, 900])
        self.assertEqual([step['env'] for step in spec['commands']], [{'SCOPE': 'home'}] * 3)

    def test_a_tolerated_status_only_applies_to_the_step_that_declared_it(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            spec = al._loopentry.command_spec({'command': {'commands': [
                {'argv': self._exit(root, 'one.py', 1), 'allow_status': [0, 1]},
                self._exit(root, 'two.py', 1)], 'timeout_sec': 30}})
            result = al._commandrun.run_command(spec, cwd=td)
            self.assertFalse(result['ok'])
            self.assertEqual(result['completedCommands'], 1)

    def test_a_list_of_lists_is_a_sequence_and_a_list_of_strings_is_one_argv(self):
        one = al._loopentry.command_spec({'command': ['agent-audit', 'collect']})
        self.assertNotIn('commands', one)
        self.assertEqual(one['argv'], ['agent-audit', 'collect'])
        many = al._loopentry.command_spec({'command': [['agent-audit', 'collect'],
                                                       ['agent-audit', 'tune', '--apply']]})
        self.assertEqual([step['argv'] for step in many['commands']],
                         [['agent-audit', 'collect'], ['agent-audit', 'tune', '--apply']])
        # 代表は先頭の段（記録と画面が 1 行で見せるもの）。
        self.assertEqual(many['argv'], ['agent-audit', 'collect'])

    def test_a_mixed_list_is_refused_with_a_pointer_to_the_sequence_spelling(self):
        with self.assertRaisesRegex(al._loopentry.LoopEntryError, "commands"):
            al._loopentry.command_spec({'command': ['agent-audit collect', ['agent-audit', 'tune']]})

    def test_argv_and_commands_cannot_both_be_written(self):
        with self.assertRaises(al._loopentry.LoopEntryError):
            al._loopentry.command_spec({'command': {'argv': ['a'], 'commands': ['b']}})

    def test_an_empty_sequence_is_refused(self):
        with self.assertRaises(al._loopentry.LoopEntryError):
            al._loopentry.command_spec({'command': {'commands': []}})

    def test_placeholders_are_filled_in_on_every_step(self):
        spec = al._loopentry.command_spec({'command': {'commands': [
            'sync --iid {iid}', {'argv': 'close --iid {iid}', 'allow_status': [0, 1]}]}})
        rendered = al._loopentry.render_command(spec, {'iid': '42'})
        self.assertEqual([step['argv'] for step in rendered['commands']],
                         [['sync', '--iid', '42'], ['close', '--iid', '42']])
        self.assertEqual(rendered['argv'], ['sync', '--iid', '42'])
        self.assertEqual(rendered['commands'][1]['allow_status'], [0, 1])

    def test_a_step_that_is_not_installed_is_skipped_without_stopping_the_rest(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            (root / 'last.py').write_text("from pathlib import Path\nPath('last').touch()\n")
            spec = al._loopentry.command_spec({'command': {'commands': [
                {'argv': f'"{sys.executable}" absent.py', 'skip_if_missing': 'absent.py'},
                f'"{sys.executable}" last.py'], 'timeout_sec': 30}})
            result = al._commandrun.run_command(spec, cwd=td)
            self.assertTrue(result['ok'])
            self.assertEqual(result['completedCommands'], 2)
            self.assertTrue((root / 'last').exists())

    def test_an_argv_array_with_a_newline_argument_is_still_one_command(self):
        # 引数の中の改行は「複数行の文字列」ではない（配列は 1 つのコマンドの引数）。
        spec = al._loopentry.command_spec({'command': ['echo', 'one\ntwo']})
        self.assertNotIn('commands', spec)
        self.assertNotIn('shell', spec)
        self.assertEqual(spec['argv'], ['echo', 'one\ntwo'])


if __name__ == "__main__":
    unittest.main()
