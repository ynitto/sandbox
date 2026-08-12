#!/usr/bin/env python3
"""Managed interactive CLI のターン完了通知契約。"""
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
import agent_loop as al  # noqa: E402


class TurnHookMailboxTest(unittest.TestCase):
    def test_managed_hook_event_round_trip(self):
        with tempfile.TemporaryDirectory() as tmp, \
             mock.patch.object(al, "_TURN_HOOKS_DIR", Path(tmp)):
            al.write_turn_hook_active(
                instance_id="instance-1",
                pane_id="%7",
                dispatch_id="dispatch-1",
                generation=3,
                agent_cli="claude",
                hook_token="secret",
            )
            with mock.patch.dict(os.environ, {
                "AGENT_LOOP_INSTANCE_ID": "instance-1",
                "AGENT_LOOP_HOOK_TOKEN": "secret",
                "AGENT_LOOP_AGENT_CLI": "claude",
                "TMUX_PANE": "%7",
            }):
                self.assertTrue(al.record_turn_hook_event(
                    adapter="claude",
                    status="complete",
                    native_event="Stop",
                ))

            event = al.claim_turn_hook_event(
                "instance-1", "%7", "dispatch-1", 3,
            )

        self.assertEqual(event["status"], "complete")
        self.assertEqual(event["adapter"], "claude")
        self.assertEqual(event["native_event"], "Stop")

    def test_slot_monitor_prefers_native_event_and_completes_once(self):
        semaphore = mock.Mock()
        complete = mock.Mock()
        failure = mock.Mock()
        hook = {
            "instance_id": "instance-1",
            "dispatch_id": "dispatch-1",
            "generation": 3,
            "agent_cli": "claude",
            "hook_token": "secret",
        }
        with tempfile.TemporaryDirectory() as tmp, \
             mock.patch.object(al, "_TURN_HOOKS_DIR", Path(tmp)):
            al.write_turn_hook_active(pane_id="%7", **hook)
            with mock.patch.dict(os.environ, {
                "AGENT_LOOP_INSTANCE_ID": "instance-1",
                "AGENT_LOOP_HOOK_TOKEN": "secret",
                "AGENT_LOOP_AGENT_CLI": "claude",
                "TMUX_PANE": "%7",
            }):
                al.record_turn_hook_event(
                    adapter="claude", status="complete", native_event="Stop",
                )
            monitor = al.SlotMonitor(semaphore)
            monitor.track("%7", on_complete=complete, on_failure=failure,
                          turn_hook=hook)
            with mock.patch.object(al.subprocess, "run") as run:
                monitor._check_pane("%7")
                monitor._check_pane("%7")

        complete.assert_called_once_with()
        failure.assert_not_called()
        semaphore.release.assert_called_once_with("%7")
        run.assert_not_called()

    def test_failure_hint_waits_for_terminal_event_then_fails(self):
        hook = {
            "instance_id": "instance-1",
            "pane_id": "%7",
            "dispatch_id": "dispatch-1",
            "generation": 3,
            "agent_cli": "opencode",
            "hook_token": "secret",
        }
        env = {
            "AGENT_LOOP_INSTANCE_ID": "instance-1",
            "AGENT_LOOP_HOOK_TOKEN": "secret",
            "AGENT_LOOP_AGENT_CLI": "opencode",
            "TMUX_PANE": "%7",
        }
        with tempfile.TemporaryDirectory() as tmp, \
             mock.patch.object(al, "_TURN_HOOKS_DIR", Path(tmp)), \
             mock.patch.dict(os.environ, env):
            al.write_turn_hook_active(**hook)
            self.assertTrue(al.record_turn_hook_event(
                adapter="opencode", status="failure-hint",
                native_event="session.error",
            ))
            self.assertIsNone(al.claim_turn_hook_event(
                "instance-1", "%7", "dispatch-1", 3,
            ))
            self.assertTrue(al.record_turn_hook_event(
                adapter="opencode", status="complete",
                native_event="session.idle",
            ))
            event = al.claim_turn_hook_event(
                "instance-1", "%7", "dispatch-1", 3,
            )

        self.assertEqual(event["status"], "failure")

    def test_slot_release_is_a_managed_kiro_event_alias(self):
        with mock.patch.object(al, "record_turn_hook_event", return_value=False) as record, \
             mock.patch.object(al, "GlobalSemaphore") as semaphore:
            with self.assertRaises(SystemExit) as exited:
                al.cmd_slot_release()

        self.assertEqual(exited.exception.code, 0)
        record.assert_called_once_with(
            adapter="kiro", status="complete", native_event="slot-release",
        )
        semaphore.assert_not_called()

    def test_hidden_hook_event_command_records_native_event(self):
        argv = [
            "agent-loop", "hook-event", "--adapter", "claude",
            "--status", "failure", "--native-event", "StopFailure",
        ]
        with mock.patch.object(sys, "argv", argv), \
             mock.patch.object(al, "record_turn_hook_event", return_value=True) as record:
            al.main()

        record.assert_called_once_with(
            adapter="claude", status="failure", native_event="StopFailure",
        )

    def test_scheduler_correlates_managed_pane_hook_with_dispatch(self):
        session = mock.Mock()
        session.get_turn_hook_context.return_value = {
            "instance_id": "instance-1",
            "agent_cli": "claude",
            "hook_token": "secret",
        }
        monitor = mock.Mock()
        scheduler = al.PeriodicScheduler(session, [], slot_monitor=monitor)
        scheduler._track_active(
            {"id": "dispatch-1", "entry_id": "prompt-1", "meta": {}},
            "%7",
            generation=3,
        )

        turn_hook = monitor.track.call_args.kwargs["turn_hook"]
        self.assertEqual(turn_hook, {
            "instance_id": "instance-1",
            "dispatch_id": "dispatch-1",
            "generation": 3,
            "agent_cli": "claude",
            "hook_token": "secret",
        })

    def test_copilot_ignores_recoverable_error(self):
        hook = {
            "instance_id": "instance-1",
            "pane_id": "%7",
            "dispatch_id": "dispatch-1",
            "generation": 3,
            "agent_cli": "copilot",
            "hook_token": "secret",
        }
        env = {
            "AGENT_LOOP_INSTANCE_ID": "instance-1",
            "AGENT_LOOP_HOOK_TOKEN": "secret",
            "AGENT_LOOP_AGENT_CLI": "copilot",
            "TMUX_PANE": "%7",
        }
        with tempfile.TemporaryDirectory() as tmp, \
             mock.patch.object(al, "_TURN_HOOKS_DIR", Path(tmp)), \
             mock.patch.dict(os.environ, env):
            al.write_turn_hook_active(**hook)
            self.assertFalse(al.handle_copilot_error('{"recoverable":true}'))
            self.assertTrue(al.handle_copilot_error('{"recoverable":false}'))
            al.record_turn_hook_event(
                adapter="copilot", status="complete", native_event="agentStop",
            )
            event = al.claim_turn_hook_event(
                "instance-1", "%7", "dispatch-1", 3,
            )

        self.assertEqual(event["status"], "failure")

    def test_duplicate_terminal_event_cannot_overwrite_first(self):
        hook = {
            "instance_id": "instance-1",
            "pane_id": "%7",
            "dispatch_id": "dispatch-1",
            "generation": 3,
            "agent_cli": "claude",
            "hook_token": "secret",
        }
        env = {
            "AGENT_LOOP_INSTANCE_ID": "instance-1",
            "AGENT_LOOP_HOOK_TOKEN": "secret",
            "AGENT_LOOP_AGENT_CLI": "claude",
            "TMUX_PANE": "%7",
        }
        with tempfile.TemporaryDirectory() as tmp, \
             mock.patch.object(al, "_TURN_HOOKS_DIR", Path(tmp)), \
             mock.patch.dict(os.environ, env):
            al.write_turn_hook_active(**hook)
            self.assertTrue(al.record_turn_hook_event(
                adapter="claude", status="complete", native_event="Stop",
            ))
            self.assertFalse(al.record_turn_hook_event(
                adapter="claude", status="failure", native_event="StopFailure",
            ))
            event = al.claim_turn_hook_event(
                "instance-1", "%7", "dispatch-1", 3,
            )

        self.assertEqual(event["status"], "complete")

    def test_screen_idle_after_failure_hint_uses_failure_callback(self):
        semaphore = mock.Mock()
        complete = mock.Mock()
        failure = mock.Mock()
        profile = mock.Mock()
        profile.is_idle.return_value = True
        hook = {
            "instance_id": "instance-1",
            "dispatch_id": "dispatch-1",
            "generation": 3,
            "agent_cli": "opencode",
            "hook_token": "secret",
        }
        env = {
            "AGENT_LOOP_INSTANCE_ID": "instance-1",
            "AGENT_LOOP_HOOK_TOKEN": "secret",
            "AGENT_LOOP_AGENT_CLI": "opencode",
            "TMUX_PANE": "%7",
        }
        with tempfile.TemporaryDirectory() as tmp, \
             mock.patch.object(al, "_TURN_HOOKS_DIR", Path(tmp)), \
             mock.patch.dict(os.environ, env):
            monitor = al.SlotMonitor(semaphore)
            monitor.track("%7", on_complete=complete, on_failure=failure,
                          profile=profile, turn_hook=hook)
            monitor._pending["%7"]["state"] = "processing"
            al.record_turn_hook_event(
                adapter="opencode", status="failure-hint",
                native_event="session.error",
            )
            with mock.patch.object(al.subprocess, "run", return_value=mock.Mock(returncode=0)), \
                 mock.patch.object(al, "_capture_pane", return_value="idle"):
                monitor._check_pane("%7")

        failure.assert_called_once_with()
        complete.assert_not_called()


if __name__ == "__main__":
    unittest.main()
