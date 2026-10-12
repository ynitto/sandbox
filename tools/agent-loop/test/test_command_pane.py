"""Real isolated tmux: terminal input, exit status, reuse, timeout, and closed panes."""
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import threading
import time
import unittest
import uuid
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import agent_loop as al
from agentcore import commandrun


class CommandPaneTest(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.root = Path(self.directory.name)
        self.socket = 'command-test-' + uuid.uuid4().hex[:10]
        reply = self.tmux('new-session', '-d', '-s', 'test', '-x', '120', '-y', '40')
        self.assertEqual(reply.returncode, 0, reply.stderr)
        self.manager = al.SessionManager.__new__(al.SessionManager)
        self.manager._lock = threading.RLock()
        self.manager._create_worker_pane = self.open_pane
        self.manager._pane_exists = lambda p: self.tmux(
            'display-message', '-p', '-t', p, '#{pane_id}').returncode == 0
        self.pane_ready = threading.Event()
        self.pane = None
        self.patch = mock.patch.object(al, '_tmux_cmd', self.tmux)
        self.patch.start()

    def tearDown(self):
        self.patch.stop()
        self.tmux('kill-server')
        self.directory.cleanup()

    def tmux(self, *args, capture=True):
        return subprocess.run(['tmux', '-L', self.socket, *args],
                              capture_output=True, text=True)

    def open_pane(self, command, cwd):
        reply = self.tmux('split-window', '-d', '-P', '-F', '#{pane_id}',
                          '-t', 'test', '-c', cwd, command)
        if reply.returncode:
            raise commandrun.CommandRunError(reply.stderr)
        self.pane = reply.stdout.strip()
        self.pane_ready.set()
        return self.pane

    def run_spec(self, spec):
        return commandrun.run_command(spec, cwd=str(self.root),
            log_file=str(self.root / 'execution.jsonl'),
            terminal=lambda argv, **kwargs: self.manager.run_command_in_pane(
                'entry', 'interactive-test', argv, **kwargs))

    def wait_prompt(self, text):
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline:
            if self.pane and text in self.tmux('capture-pane', '-p', '-t', self.pane).stdout:
                return
            time.sleep(0.05)
        self.fail('prompt not visible: ' + text)

    def test_input_has_a_controlling_tty_and_is_recorded(self):
        script = self.root / 'interactive.py'
        script.write_text("import os, sys\nassert sys.stdin.isatty()\n"
                          "assert os.isatty(os.open('/dev/tty', os.O_RDONLY))\n"
                          "print('answer=' + input('QUESTION: '), flush=True)\n")
        result = {}
        thread = threading.Thread(target=lambda: result.update(self.run_spec(
            {'argv': [sys.executable, str(script)], 'timeout_sec': 10})))
        thread.start()
        try:
            self.wait_prompt('QUESTION:')
            self.tmux('send-keys', '-t', self.pane, '-l', 'hello world')
            self.tmux('send-keys', '-t', self.pane, 'Enter')
        finally:
            thread.join(12)
        self.assertFalse(thread.is_alive())
        self.assertTrue(result['ok'], result)
        self.assertIn('answer=hello world', result['stdout'])
        self.assertIn('command_terminal', (self.root / 'execution.jsonl').read_text())

    def test_sequence_reuses_one_pane_and_keeps_exit_status(self):
        (self.root / 'first.py').write_text("print('first')\n")
        (self.root / 'second.py').write_text("print('second')\nraise SystemExit(3)\n")
        spec = al._loopentry.command_spec({'command': {'commands': [
            [sys.executable, 'first.py'],
            {'argv': [sys.executable, 'second.py'],
             'allow_status': [0, 3]}]}})
        result = self.run_spec(spec)
        self.assertTrue(result['ok'], result)
        self.assertEqual(result['completedCommands'], 2)
        self.assertEqual(result['stdout'].count('first'), 1)
        panes = self.tmux('list-panes', '-t', 'test', '-F', '#{pane_id}').stdout.splitlines()
        self.assertEqual(len(panes), 2)  # controller plus reused command pane

    def test_timeout_terminates_children(self):
        script = self.root / 'timeout.py'
        script.write_text("import subprocess, sys, time\n"
            "subprocess.Popen([sys.executable, '-c', "
            "\"import time;from pathlib import Path;time.sleep(2);Path('leaked').touch()\"])\n"
            "time.sleep(20)\n")
        result = self.run_spec({'argv': [sys.executable, str(script)], 'timeout_sec': 1})
        self.assertFalse(result['ok'])
        self.assertEqual(result['stopReason'], 'command_timeout')
        time.sleep(2)
        self.assertFalse((self.root / 'leaked').exists())

    def test_closing_an_input_pane_fails_instead_of_completing(self):
        result = {}
        thread = threading.Thread(target=lambda: result.update(self.run_spec(
            {'argv': [sys.executable, '-c', "input('WAITING: ' )"], 'timeout_sec': 10})))
        thread.start()
        try:
            self.wait_prompt('WAITING:')
            self.tmux('kill-pane', '-t', self.pane)
        finally:
            thread.join(12)
        self.assertFalse(thread.is_alive())
        self.assertFalse(result['ok'], result)
        self.assertNotEqual(result['stopReason'], 'command_timeout')
        self.assertLess(result['durationSec'], 3)


if __name__ == '__main__':
    unittest.main()
