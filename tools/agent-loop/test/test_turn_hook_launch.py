#!/usr/bin/env python3
"""Managed interactive pane だけへturn-completion adapterを注入する。"""
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
import agent_loop as al  # noqa: E402


class TurnHookLaunchTest(unittest.TestCase):
    def test_additive_adapters_preserve_ambient_configuration(self):
        assets = Path(__file__).resolve().parents[1] / "agent-hooks"
        with tempfile.TemporaryDirectory() as tmp:
            for adapter in ("claude", "copilot", "opencode"):
                with self.subTest(adapter=adapter):
                    argv, env, cleanup = al.prepare_turn_hook_launch(
                        adapter=adapter,
                        argv=[adapter],
                        env={"HOME": "/real/home", "EXISTING": "yes"},
                        instance_id="instance-1",
                        hook_token="secret",
                        executable="/opt/bin/agent-loop",
                        assets_dir=assets,
                        runtime_root=Path(tmp),
                    )
                    self.assertEqual(env["HOME"], "/real/home")
                    self.assertEqual(env["EXISTING"], "yes")
                    self.assertEqual(env["AGENT_LOOP_INSTANCE_ID"], "instance-1")
                    self.assertEqual(env["AGENT_LOOP_HOOK_TOKEN"], "secret")
                    self.assertEqual(env["AGENT_LOOP_AGENT_CLI"], adapter)
                    self.assertNotIn("COPILOT_HOME", env)
                    self.assertNotIn("CODEX_HOME", env)
                    if adapter in ("claude", "copilot"):
                        self.assertIn("--plugin-dir", argv)
                        self.assertIsNone(cleanup)
                    else:
                        config_dir = Path(env["OPENCODE_CONFIG_DIR"])
                        self.assertEqual(cleanup, config_dir)
                        self.assertTrue((config_dir / "plugins" / "agent-loop.js").is_file())
                        self.assertFalse((config_dir / "opencode.json").exists())

    def test_session_manager_registers_hook_only_for_managed_pane(self):
        profile = al.CliProfile("claude", {
            "interactive": {
                "command": ["claude"],
                "turn_completion": "claude",
            },
        }, argv=["claude"])
        with tempfile.TemporaryDirectory() as tmp, \
             mock.patch.object(al, "_CLI_PROFILE", profile), \
             mock.patch.object(al.shutil, "which", side_effect=lambda name: f"/bin/{name}"), \
             mock.patch.object(al, "run_session_commands", return_value=True):
            mgr = al.SessionManager(
                target_path=tmp,
                instance_id="instance-1",
                kiro_args_base=[],
                split_direction="horizontal",
                startup_timeout=1,
            )
            with mock.patch.object(mgr, "_create_worker_pane", return_value="%9") as create, \
                 mock.patch.object(mgr, "_send_session_chat_commands"), \
                 mock.patch.object(mgr, "write_state"):
                self.assertTrue(mgr._start_pane("prompt-1", "prompt"))

            context = mgr.get_turn_hook_context("%9")

        self.assertEqual(context["instance_id"], "instance-1")
        self.assertEqual(context["agent_cli"], "claude")
        self.assertTrue(context["hook_token"])
        command = create.call_args.args[0]
        self.assertIn("AGENT_LOOP_HOOK_TOKEN=", command)
        self.assertIn("--plugin-dir", command)


if __name__ == "__main__":
    unittest.main()
