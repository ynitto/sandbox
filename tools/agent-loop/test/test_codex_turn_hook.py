#!/usr/bin/env python3
"""Codex notifyをsession-localに多重化する。"""
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
import agent_loop as al  # noqa: E402


class CodexTurnHookTest(unittest.TestCase):
    def test_existing_profile_notify_runs_after_managed_completion(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            home = root / "home"
            codex_home = home / ".codex"
            codex_home.mkdir(parents=True)
            (codex_home / "config.toml").write_text(
                'notify = ["base-notify"]\nprofile = "work"\n', encoding="utf-8",
            )
            (codex_home / "work.config.toml").write_text(
                'notify = ["profile-notify", "--quiet"]\n', encoding="utf-8",
            )
            argv, env, cleanup = al.prepare_turn_hook_launch(
                adapter="codex",
                argv=["codex"],
                env={"HOME": str(home)},
                instance_id="instance-1",
                hook_token="secret",
                executable="/opt/bin/agent-loop",
                user_home=home,
            )

            self.assertIsNone(cleanup)
            self.assertNotIn("CODEX_HOME", env)
            self.assertEqual(
                json.loads(env["AGENT_LOOP_CODEX_NOTIFY"]),
                ["profile-notify", "--quiet"],
            )
            override = argv[argv.index("--config") + 1]
            self.assertEqual(
                json.loads(override.removeprefix("notify="))[:2],
                ["/opt/bin/agent-loop", "hook-event"],
            )

            hook = {
                "instance_id": "instance-1",
                "pane_id": "%7",
                "dispatch_id": "dispatch-1",
                "generation": 3,
                "agent_cli": "codex",
                "hook_token": "secret",
            }
            with mock.patch.object(al, "_TURN_HOOKS_DIR", root / "mailbox"), \
                 mock.patch.dict(os.environ, {**env, "TMUX_PANE": "%7"}), \
                 mock.patch.object(al.subprocess, "run") as run:
                al.write_turn_hook_active(**hook)
                payload = '{"type":"agent-turn-complete"}'
                self.assertTrue(al.handle_codex_notify(payload))
                event = al.claim_turn_hook_event(
                    "instance-1", "%7", "dispatch-1", 3,
                )

            self.assertEqual(event["status"], "complete")
            run.assert_called_once_with(
                ["profile-notify", "--quiet", payload],
                stdin=al.subprocess.DEVNULL,
                stdout=al.subprocess.DEVNULL,
                stderr=al.subprocess.DEVNULL,
                check=False,
                timeout=10,
            )


if __name__ == "__main__":
    unittest.main()
