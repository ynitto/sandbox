"""InboxWatcher._try_dispatch のセマフォ解放/追跡テスト（回帰）。

send_prompt 失敗時にセマフォスロットがリークしないこと、成功時は scheduler と
同様に SlotMonitor.track が呼ばれることを確認する。tmux / エージェント CLI 不要。

    python3 -m pytest tools/agent-loop/test/ -q
"""
import os
import sys
import unittest
from unittest import mock
from types import SimpleNamespace

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
import agent_loop as al  # noqa: E402


class _FakeSemaphore:
    def __init__(self):
        self.acquired = []
        self.released = []

    def acquire(self, pane_id):
        self.acquired.append(pane_id)
        return True

    def release(self, pane_id):
        self.released.append(pane_id)


class _FakeSlotMonitor:
    def __init__(self):
        self.tracked = []

    def track(self, pane_id):
        self.tracked.append(pane_id)


class _FakeSessionManager:
    def __init__(self, send_ok):
        self._send_ok = send_ok

    def ensure_session(self, prompt_id, name):
        return True

    def get_pane_id(self, prompt_id):
        return "%1"

    def send_prompt(self, prompt_id, prompt_text):
        return self._send_ok


class InboxDispatchTests(unittest.TestCase):
    def _watcher(self, send_ok):
        semaphore = _FakeSemaphore()
        slot_monitor = _FakeSlotMonitor()
        watcher = al.InboxWatcher(
            agent_name="agent-a",
            session_mgr=_FakeSessionManager(send_ok),
            semaphore=semaphore,
            slot_monitor=slot_monitor,
        )
        return watcher, semaphore, slot_monitor

    def test_send_failure_releases_slot(self):
        watcher, semaphore, slot_monitor = self._watcher(send_ok=False)
        ok = watcher._try_dispatch({"id": "m1", "from": "agent-b", "body": "hi"})
        self.assertFalse(ok)
        self.assertEqual(semaphore.acquired, ["%1"])
        self.assertEqual(semaphore.released, ["%1"])  # リークしていないこと
        self.assertEqual(slot_monitor.tracked, [])

    def test_send_success_tracks_slot_without_releasing(self):
        watcher, semaphore, slot_monitor = self._watcher(send_ok=True)
        ok = watcher._try_dispatch({"id": "m2", "from": "agent-b", "body": "hi"})
        self.assertTrue(ok)
        self.assertEqual(semaphore.acquired, ["%1"])
        self.assertEqual(semaphore.released, [])  # 解放は SlotMonitor 側の役目
        self.assertEqual(slot_monitor.tracked, ["%1"])


class AutoAttachTests(unittest.TestCase):
    def test_reexecutes_original_entrypoint(self):
        args = SimpleNamespace(controller_mode=False, no_auto_attach=False,
                               instance_id="test", log_level=None, split_direction=None)
        with mock.patch.object(al.os, "environ", {}), \
             mock.patch.object(al.sys, "argv", ["/opt/agent-loop"]), \
             mock.patch.object(al.shutil, "which", return_value="/usr/bin/tmux"), \
             mock.patch.object(al.subprocess, "run", return_value=SimpleNamespace(returncode=1)), \
             mock.patch.object(al.os, "execvp") as execvp:
            al._auto_attach_tmux_if_needed(args)
        command = execvp.call_args_list[0].args[1][-1]
        self.assertIn("/opt/agent-loop", command)
        self.assertNotIn("agent_loop/__init__.py", command)


if __name__ == "__main__":
    unittest.main()
