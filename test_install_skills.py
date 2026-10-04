"""Kiro / Copilot IDE 用スキルは CLI が無くても配置できる。"""
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import install


class IdeSkillInstallTest(unittest.TestCase):
    def test_caveman_without_cli_or_npx(self):
        for agent in ("kiro", "copilot"):
            with self.subTest(agent=agent), tempfile.TemporaryDirectory() as home, \
                 mock.patch.object(install.os.path, "expanduser", return_value=home), \
                 mock.patch.object(install.shutil, "which", side_effect=AssertionError("CLI 検出不要")), \
                 mock.patch.object(install.subprocess, "run", side_effect=AssertionError("外部コマンド不要")):
                self.assertTrue(install.setup_caveman(agent))
                dest = Path(home) / f".{agent}/skills/caveman/SKILL.md"
                self.assertTrue(dest.is_file())
                dest.write_text("利用者のスキル", encoding="utf-8")
                self.assertTrue(install.setup_caveman(agent))
                self.assertEqual(dest.read_text(encoding="utf-8"), "利用者のスキル")
                self.assertTrue(install.setup_caveman(agent, force=True))
                self.assertIn("name: caveman", dest.read_text(encoding="utf-8"))

    def test_graphify_skill_survives_missing_runtime_and_installers(self):
        for agent in ("kiro", "copilot"):
            with self.subTest(agent=agent), tempfile.TemporaryDirectory() as home, \
                 mock.patch.object(install.os.path, "expanduser", return_value=home), \
                 mock.patch.object(install.shutil, "which", return_value=None), \
                 mock.patch.object(install, "_cli_version_string", return_value=None), \
                 mock.patch.object(install, "_pypi_latest_version", return_value=None), \
                 mock.patch.object(install.subprocess, "run", side_effect=AssertionError("実行できる CLI 無し")):
                self.assertTrue(install.setup_graphify(agent))
                dest = Path(home) / f".{agent}/skills/graphify"
                source = Path(install.REPO_ROOT) / "tools/codd-agent/machine/skills/graphify"
                for file in source.rglob("*"):
                    if file.is_file():
                        self.assertEqual((dest / file.relative_to(source)).read_bytes(), file.read_bytes())


if __name__ == "__main__":
    unittest.main()
