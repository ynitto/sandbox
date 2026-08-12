#!/usr/bin/env python3
"""Kiro classic turn hook用private homeの継承契約。"""
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
import agent_loop as al  # noqa: E402


class KiroTurnHookTest(unittest.TestCase):
    def test_private_home_keeps_resources_without_copying_runtime_state(self):
        assets = Path(__file__).resolve().parents[1] / "agent-hooks"
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            home = root / "home"
            workspace = root / "work"
            runtime = root / "runtime"
            (home / ".kiro" / "agents").mkdir(parents=True)
            (home / ".kiro" / "prompts").mkdir()
            (home / ".kiro" / "skills" / "global").mkdir(parents=True)
            (home / ".kiro" / "steering").mkdir()
            (home / ".kiro" / "settings").mkdir()
            (home / ".kiro" / "sessions").mkdir()
            (workspace / ".kiro" / "agents").mkdir(parents=True)
            (workspace / ".kiro" / "skills" / "local").mkdir(parents=True)
            (workspace / ".kiro" / "steering").mkdir()
            (workspace / "AGENTS.md").write_text("project rules", encoding="utf-8")
            (home / ".kiro" / "prompts" / "p.md").write_text("prompt", encoding="utf-8")
            (home / ".kiro" / "skills" / "global" / "SKILL.md").write_text("skill", encoding="utf-8")
            (home / ".kiro" / "steering" / "rules.md").write_text("rules", encoding="utf-8")
            (home / ".kiro" / "settings" / "cli.json").write_text("{}", encoding="utf-8")
            (home / ".kiro" / "settings" / "mcp.json").write_text('{"mcpServers": {}}', encoding="utf-8")
            (home / ".kiro" / "sessions" / "secret.json").write_text("secret", encoding="utf-8")
            (home / ".kiro" / "auth.json").write_text("secret", encoding="utf-8")
            (home / ".kiro" / "agents" / "review.json").write_text(
                json.dumps({"description": "global"}), encoding="utf-8",
            )
            project_agent = workspace / ".kiro" / "agents" / "review.json"
            project_agent.write_text(json.dumps({
                "description": "project",
                "hooks": {"stop": [{"command": "existing-hook"}]},
                "resources": ["file://README.md"],
            }), encoding="utf-8")
            before = project_agent.stat().st_mtime_ns

            argv, env, cleanup = al.prepare_turn_hook_launch(
                adapter="kiro",
                argv=["kiro-cli", "chat", "--agent", "review"],
                env={"HOME": str(home)},
                instance_id="instance-1",
                hook_token="secret",
                executable="/opt/bin/agent-loop",
                assets_dir=assets,
                runtime_root=runtime,
                user_home=home,
                cwd=workspace,
            )

            private_home = Path(env["KIRO_HOME"])
            generated_name = argv[argv.index("--agent") + 1]
            generated = json.loads(
                (private_home / "agents" / f"{generated_name}.json").read_text(encoding="utf-8")
            )

            self.assertEqual(generated["description"], "project")
            self.assertTrue(generated["includeMcpJson"])
            self.assertEqual(generated["hooks"]["stop"][0]["command"], "existing-hook")
            self.assertIn("hook-event --adapter kiro", generated["hooks"]["stop"][1]["command"])
            self.assertIn("file://README.md", generated["resources"])
            self.assertTrue(any("skills" in value for value in generated["resources"]))
            self.assertTrue((private_home / "settings" / "mcp.json").is_file())
            self.assertFalse((private_home / "sessions").exists())
            self.assertFalse((private_home / "auth.json").exists())
            self.assertEqual(project_agent.stat().st_mtime_ns, before)
            self.assertEqual(cleanup, private_home)


if __name__ == "__main__":
    unittest.main()
