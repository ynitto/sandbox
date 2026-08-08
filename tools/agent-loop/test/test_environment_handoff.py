"""environment handoff（Phase 2C-1）の contract テスト。"""
import os
import sys
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
import agent_loop as al  # noqa: E402


class NormalizeEnvironmentHandoffTests(unittest.TestCase):
    def test_defaults_when_missing(self):
        self.assertEqual(
            al.normalize_environment_handoff({}),
            {"prompt": False, "skill_home": None, "token_env_names": []},
        )

    def test_invalid_token_names_filtered(self):
        cfg = {
            "environment_handoff": {
                "prompt": True,
                "skill_home": "~/.agents/skills",
                "token_env_names": ["GITLAB_TOKEN", "bad-name", "lowercase", "1START"],
            },
        }
        handoff = al.normalize_environment_handoff(cfg)
        self.assertTrue(handoff["prompt"])
        self.assertEqual(handoff["token_env_names"], ["GITLAB_TOKEN"])


class EnvPromptBlockTests(unittest.TestCase):
    def test_set_unset_without_values(self):
        handoff = {
            "prompt": True,
            "skill_home": None,
            "token_env_names": ["GITLAB_TOKEN", "OPENAI_API_KEY"],
        }
        with mock.patch.dict(os.environ, {"GITLAB_TOKEN": "secret"}, clear=False):
            block = al.build_env_prompt_block(
                handoff,
                agent_cli="codex",
                agent_home=Path("/home/user/.agents"),
            )
        self.assertIn("[ENV]", block)
        self.assertIn("[/ENV]", block)
        self.assertIn("skill_home=UNSET", block)
        self.assertIn("token.GITLAB_TOKEN=SET", block)
        self.assertIn("token.OPENAI_API_KEY=UNSET", block)
        self.assertNotIn("secret", block)
        self.assertNotIn("PATH=", block)

    def test_skill_home_existing_directory(self):
        with mock.patch("pathlib.Path.is_dir", return_value=True), \
             mock.patch("pathlib.Path.resolve", return_value=Path("/resolved/skills")):
            block = al.build_env_prompt_block(
                {
                    "prompt": True,
                    "skill_home": "/any/path",
                    "token_env_names": [],
                },
                agent_cli="codex",
                agent_home=Path("/home/user/.agents"),
            )
        self.assertIn("skill_home=/resolved/skills", block)


class SchedulerEnvPrependTests(unittest.TestCase):
    def test_ralph_child_skips_env_block(self):
        scheduler = al.PeriodicScheduler(
            mock.Mock(),
            [],
            semaphore=None,
            slot_monitor=None,
        )
        scheduler.configure_runtime(
            environment_handoff={
                "prompt": True,
                "skill_home": None,
                "token_env_names": [],
            },
        )
        prompt = scheduler._maybe_prepend_env_block(  # noqa: SLF001
            "work body",
            {"source": "ralph"},
        )
        self.assertEqual(prompt, "work body")

    def test_root_prompt_gets_env_block(self):
        scheduler = al.PeriodicScheduler(
            mock.Mock(),
            [],
            semaphore=None,
            slot_monitor=None,
        )
        scheduler.configure_runtime(
            environment_handoff={
                "prompt": True,
                "skill_home": None,
                "token_env_names": [],
            },
        )
        prompt = scheduler._maybe_prepend_env_block(  # noqa: SLF001
            "work body",
            {"source": "send"},
        )
        self.assertTrue(prompt.startswith("[ENV]"))
        self.assertIn("work body", prompt)


class LaunchEnvTests(unittest.TestCase):
    def test_launch_env_sets_home_and_agent_home(self):
        mgr = al.SessionManager(
            target_path="/tmp",
            instance_id="test",
            kiro_args_base=[],
            split_direction="horizontal",
            startup_timeout=1,
        )
        mgr.configure_environment_handoff({}, user_home="/home/tester")
        with mock.patch.object(al, "_CLI_PROFILE", al.CliProfile(name="codex", spec={"env": {"FOO": "bar"}})):
            env = mgr._build_launch_env()  # noqa: SLF001
        self.assertEqual(env["HOME"], str(Path("/home/tester").resolve()))
        self.assertEqual(env["AGENT_HOME"], str(al.agent_home_dir().resolve()))
        self.assertEqual(env["FOO"], "bar")
        self.assertNotIn("PATH", env)

    def test_launch_command_prefixes_env(self):
        mgr = al.SessionManager(
            target_path="/tmp",
            instance_id="test",
            kiro_args_base=[],
            split_direction="horizontal",
            startup_timeout=1,
        )
        cmd = mgr._format_launch_command(  # noqa: SLF001
            ["/bin/codex", "chat"],
            {"HOME": "/home/u", "AGENT_HOME": "/home/u/.agents"},
        )
        self.assertTrue(cmd.startswith("env "))
        self.assertIn("HOME=/home/u", cmd)
        self.assertIn("AGENT_HOME=/home/u/.agents", cmd)
        self.assertIn("/bin/codex", cmd)
        self.assertIn("chat", cmd)


if __name__ == "__main__":
    unittest.main()
