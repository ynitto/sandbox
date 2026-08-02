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
        self.on_complete = None

    def track(self, pane_id, on_complete=None):
        self.tracked.append(pane_id)
        self.on_complete = on_complete


class _FakeSessionManager:
    def __init__(self, send_ok):
        self._send_ok = send_ok
        self.removed = []

    def ensure_session(self, prompt_id, name, owner="scheduled"):
        return True

    def get_pane_id(self, prompt_id):
        return "%1"

    def send_prompt(self, prompt_id, prompt_text):
        return self._send_ok

    def remove_session(self, prompt_id, **kwargs):
        self.removed.append((prompt_id, kwargs))


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

    def test_send_success_removes_session_after_completion(self):
        watcher, semaphore, slot_monitor = self._watcher(send_ok=True)
        ok = watcher._try_dispatch({"id": "m2", "from": "agent-b", "body": "hi"})
        self.assertTrue(ok)
        self.assertEqual(semaphore.acquired, ["%1"])
        self.assertEqual(semaphore.released, [])  # 解放は SlotMonitor 側の役目
        self.assertEqual(slot_monitor.tracked, ["%1"])
        self.assertIsNotNone(slot_monitor.on_complete)
        slot_monitor.on_complete()
        self.assertEqual(
            watcher._session_mgr.removed,
            [("inbox-m2", {"owner": "inbox", "pane_id": "%1"})],
        )

    def test_start_timeout_removes_unstarted_session(self):
        semaphore = _FakeSemaphore()
        monitor = al.SlotMonitor(semaphore)
        on_complete = mock.Mock()

        monitor.track("%1", on_complete=on_complete)
        monitor._pending["%1"]["acquired_at"] = 0
        with mock.patch.object(al.subprocess, "run", return_value=SimpleNamespace(returncode=0)), \
             mock.patch.object(al, "_capture_pane", return_value="prompt"), \
             mock.patch.object(al, "_pane_has_prompt", return_value=True), \
             mock.patch.object(al.time, "time", return_value=61):
            monitor._check_pane("%1")
        on_complete.assert_called_once_with()
        self.assertNotIn("%1", monitor._pending)

    def test_failed_cleanup_is_retried_without_releasing_slot_twice(self):
        semaphore = _FakeSemaphore()
        monitor = al.SlotMonitor(semaphore)
        on_complete = mock.Mock(side_effect=[RuntimeError("busy"), None])
        monitor.track("%1", on_complete=on_complete)

        monitor._release("%1", notify_complete=True)
        self.assertIn("%1", monitor._pending)
        monitor._release("%1", notify_complete=True)

        self.assertEqual(on_complete.call_count, 2)
        self.assertEqual(semaphore.released, ["%1"])
        self.assertNotIn("%1", monitor._pending)

    def test_processing_timeout_keeps_cleanup_monitor_until_completion(self):
        semaphore = _FakeSemaphore()
        monitor = al.SlotMonitor(semaphore, slot_timeout_seconds=10)
        on_complete = mock.Mock()
        monitor.track("%1", on_complete=on_complete)
        monitor._pending["%1"].update(state="processing", acquired_at=0)

        with mock.patch.object(al.subprocess, "run", return_value=SimpleNamespace(returncode=0)), \
             mock.patch.object(al, "_capture_pane", return_value="working"), \
             mock.patch.object(al, "_pane_has_prompt", return_value=False), \
             mock.patch.object(al.time, "time", return_value=11):
            monitor._check_pane("%1")
        on_complete.assert_not_called()
        self.assertIn("%1", monitor._pending)

        with mock.patch.object(al.subprocess, "run", return_value=SimpleNamespace(returncode=0)), \
             mock.patch.object(al, "_capture_pane", return_value="prompt"), \
             mock.patch.object(al, "_pane_has_prompt", return_value=True):
            monitor._check_pane("%1")
        on_complete.assert_called_once_with()

    def test_remove_session_drops_it_from_restart_management(self):
        manager = al.SessionManager.__new__(al.SessionManager)
        manager._lock = al.threading.Lock()
        manager._panes = {"inbox-m3": "%3"}
        manager._prompt_names = {"inbox-m3": "inbox:agent-b"}
        manager._tmux_names = {"inbox-m3": "tmux"}
        manager._prompt_cwds = {"inbox-m3": None}
        manager._owners = {"inbox-m3": "inbox"}
        manager._instr_rev = {"inbox-m3": 1}
        manager._restart_locks = {}
        manager._pane_exists = mock.Mock(return_value=False)
        manager.write_state = mock.Mock()

        manager.remove_session("inbox-m3")
        manager.restart_pane = mock.Mock()
        manager.restart_if_dead()

        self.assertEqual(manager._panes, {})
        self.assertEqual(manager._prompt_names, {})
        self.assertEqual(manager._restart_locks, {})
        manager.restart_pane.assert_not_called()

    def test_inbox_cannot_reuse_or_remove_scheduled_session(self):
        manager = al.SessionManager.__new__(al.SessionManager)
        manager._lock = al.threading.Lock()
        manager._panes = {"shared": "%4"}
        manager._owners = {"shared": "scheduled"}
        manager._prompt_cwds = {"shared": None}
        manager._start_pane = mock.Mock()

        self.assertFalse(manager.ensure_session("shared", "inbox", owner="inbox"))
        manager.remove_session("shared", owner="inbox", pane_id="%4")

        self.assertEqual(manager._panes, {"shared": "%4"})
        manager._start_pane.assert_not_called()

    def test_config_sync_does_not_overwrite_active_inbox_session(self):
        manager = al.SessionManager.__new__(al.SessionManager)
        manager._lock = al.threading.Lock()
        manager._panes = {"shared": "%5"}
        manager._owners = {"shared": "inbox"}
        manager._prompt_names = {"shared": "inbox:agent-b"}
        manager._tmux_names = {"shared": "tmux"}
        manager._prompt_cwds = {"shared": None}
        manager._start_pane = mock.Mock()
        manager.write_state = mock.Mock()

        manager.sync_entries([{"id": "shared", "name": "scheduled"}])

        self.assertEqual(manager._panes, {"shared": "%5"})
        manager._start_pane.assert_not_called()


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
