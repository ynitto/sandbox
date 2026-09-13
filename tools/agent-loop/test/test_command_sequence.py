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
        self.assertEqual(result['commands'], [['echo', 'two words'], ['echo', 'two words']])
        with self.assertRaises(al._loopentry.LoopEntryError):
            al._loopentry.command_spec({'command': 'echo valid\necho invalid | cat'})

    def test_argv_array_with_a_newline_argument_is_still_one_command(self):
        spec = al._loopentry.command_spec({'command': ['echo', 'one\ntwo']})
        self.assertNotIn('commands', spec)
        self.assertEqual(spec['argv'], ['echo', 'one\ntwo'])
