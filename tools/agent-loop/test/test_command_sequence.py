"""改行区切りのコマンドを同じ実行経路で順番に実行する。"""
import sys
import tempfile
import unittest
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import agent_loop as al


class CommandSequenceTest(unittest.TestCase):
    def test_lines_run_in_order_with_blank_lines_crlf_and_shared_environment(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            (root / 'first.py').write_text("import os\nfrom pathlib import Path\nPath('value').write_text(os.environ['VALUE'])\nprint('first')\n")
            (root / 'second.py').write_text("from pathlib import Path\nprint(Path('value').read_text())\n")
            spec = al._loopentry.command_spec({'command': {
                'argv': f'"{sys.executable}" first.py\r\n\r\n"{sys.executable}" second.py\r\n',
                'env': {'VALUE': 'second'}, 'timeout_sec': 5,
            }})
            result = al._commandrun.run_command(spec, cwd=td)
            self.assertTrue(result['ok'])
            self.assertEqual(result['stdout'], 'first\nsecond\n')
            self.assertEqual(result['completedCommands'], 2)

    def test_failure_stops_remaining_commands(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            (root / 'fail.py').write_text('raise SystemExit(3)\n')
            (root / 'later.py').write_text("from pathlib import Path\nPath('must-not-exist').touch()\n")
            spec = al._loopentry.command_spec({'command': f'"{sys.executable}" fail.py\n"{sys.executable}" later.py'})
            result = al._commandrun.run_command(spec, cwd=td)
            self.assertFalse(result['ok'])
            self.assertEqual(result['status'], 3)
            self.assertEqual(result['completedCommands'], 0)
            self.assertFalse((root / 'must-not-exist').exists())

    def test_placeholders_render_on_every_line_and_invalid_later_line_is_rejected(self):
        spec = al._loopentry.command_spec({'command': 'echo {value}\necho {value}'})
        result = al._loopentry.render_command(spec, {'value': 'two words'})
        self.assertEqual([step['argv'] for step in result['commands']],
                         [['echo', 'two words'], ['echo', 'two words']])
        with self.assertRaises(al._loopentry.LoopEntryError):
            al._loopentry.command_spec({'command': 'echo valid\necho invalid | cat'})

    def test_argv_array_with_a_newline_argument_is_still_one_command(self):
        spec = al._loopentry.command_spec({'command': ['echo', 'one\ntwo']})
        self.assertNotIn('commands', spec)
        self.assertEqual(spec['argv'], ['echo', 'one\ntwo'])


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
