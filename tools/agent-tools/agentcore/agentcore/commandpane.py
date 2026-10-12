"""Run an argv in a user-operable tmux terminal and return its observed exit status."""
from __future__ import annotations

import json
import os
from pathlib import Path
import shlex
import signal
import sys
import tempfile
import time

from agentcore.commandrun import CommandRunError
from agentcore.harness.toolloop import _tl_append_log

# The worker is self-contained so source checkouts and installed zipapps use the same runner.
# Children inherit the pane's controlling terminal and foreground process group.
_WORKER = r'''
import json, os, subprocess, sys, time
from pathlib import Path
root = Path(sys.argv[1])
while not (root / 'go').exists():
    time.sleep(0.05)
request = json.loads((root / 'request.json').read_text())
(root / 'request.json').unlink()
try:
    child = subprocess.Popen(request['argv'], cwd=request['cwd'], env=request['env'])
    status = child.wait()
    result = {'status': status, 'error': ''}
except OSError as exc:
    result = {'status': None, 'error': str(exc)}
print('\n[agent-loop] exit status: ' + str(result['status']), flush=True)
(root / 'result.tmp').write_text(json.dumps(result))
os.replace(root / 'result.tmp', root / 'result.json')
'''


def run_in_pane(argv, *, cwd, env, timeout, log_file, open_pane, tmux):
    """`open_pane(command, cwd)` returns a pane ID; `tmux(*args)` runs tmux commands.

    stdout/stderr share the TTY. The full terminal stream is retained next to the JSONL log.
    A closed pane is failure; a timeout terminates its foreground process group including children.
    """
    with tempfile.TemporaryDirectory(prefix='agent-loop-tty-') as directory:
        root = Path(directory)
        request = root / 'request.json'
        request.write_text(json.dumps({'argv': argv, 'cwd': cwd, 'env': env}))
        request.chmod(0o600)
        output = (Path(str(log_file) + '.' + root.name + '.terminal.log')
                  if log_file else root / 'output.log')
        output.parent.mkdir(parents=True, exist_ok=True)
        output.touch(mode=0o600)
        command = shlex.join([sys.executable, '-c', _WORKER, str(root)])
        pane = None
        group = None
        timed_out = False
        result = {'status': None, 'error': '実行ペインが閉じられました'}
        try:
            pane = open_pane(command, cwd)
            if not pane:
                raise CommandRunError('コマンド実行用 tmux ペインを作成できません')
            for args in (('set-option', '-p', '-t', pane, 'remain-on-exit', 'on'),
                         ('pipe-pane', '-O', '-t', pane,
                          'cat >> ' + shlex.quote(str(output)))):
                reply = tmux(*args)
                if reply.returncode:
                    raise CommandRunError(reply.stderr or 'tmux ペインの準備に失敗しました')
            pid = tmux('display-message', '-p', '-t', pane, '#{pane_pid}')
            if pid.returncode == 0:
                try:
                    group = os.getpgid(int(pid.stdout.strip()))
                except (ValueError, ProcessLookupError):
                    group = None  # ペインのプロセスが既に終わっている。後段は結果ファイルで判定する
            if log_file:
                _tl_append_log(log_file, {'event': 'command_terminal', 'paneId': pane,
                                          'terminalLog': str(output)})
            (root / 'go').touch()
            deadline = time.monotonic() + timeout
            while True:
                if (root / 'result.json').exists():
                    result = json.loads((root / 'result.json').read_text())
                    break
                state = tmux('list-panes', '-a', '-F', '#{pane_id}:#{pane_dead}')
                panes = dict(line.split(':', 1) for line in state.stdout.splitlines()
                             if ':' in line)
                if state.returncode or panes.get(pane) != '0':
                    break
                if time.monotonic() >= deadline:
                    timed_out = True
                    break
                time.sleep(0.1)
        finally:
            if pane:
                if timed_out or not (root / 'result.json').exists():
                    # Never signal the daemon's own process group.
                    if group and group != os.getpgrp():
                        for sig in (signal.SIGTERM, signal.SIGKILL):
                            try:
                                os.killpg(group, sig)
                            except ProcessLookupError:
                                break
                            if sig == signal.SIGTERM:
                                time.sleep(0.2)
                    tmux('kill-pane', '-t', pane)
                # Let tmux flush pending terminal bytes, then close only our output pipe.
                time.sleep(0.1)
                tmux('pipe-pane', '-t', pane)
        return output.read_bytes(), result['error'].encode(), result['status'], timed_out
