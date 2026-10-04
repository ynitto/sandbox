"""外部スキルは配布元から取得し、エージェント CLI が無くても IDE に配置する。"""
import io
import tempfile
import unittest
import zipfile
from pathlib import Path
from unittest import mock

import install


def archive_bytes(files):
    data = io.BytesIO()
    with zipfile.ZipFile(data, "w") as archive:
        for path, body in files.items():
            archive.writestr(f"caveman-main/{path}", body)
    return data.getvalue()


class IdeSkillInstallTest(unittest.TestCase):
    def test_caveman_downloads_from_official_source_without_cli_or_npx(self):
        data = archive_bytes({"skills/caveman/SKILL.md": "downloaded skill",
                              "skills/caveman/references/detail.md": "downloaded reference",
                              "LICENSE": "upstream license", "other/file": "do not install"})
        for agent in ("kiro", "copilot"):
            with self.subTest(agent=agent), tempfile.TemporaryDirectory() as home, \
                 mock.patch.object(install, "resolve_paths", return_value={"skill_home": f"{home}/.{agent}/skills"}), \
                 mock.patch.object(install.shutil, "which", side_effect=AssertionError("エージェント CLI 不要")), \
                 mock.patch.object(install.subprocess, "run", side_effect=AssertionError("npx 不要")), \
                 mock.patch.object(install.urllib_request, "urlopen", side_effect=lambda *a, **k: io.BytesIO(data)) as fetch:
                self.assertTrue(install.setup_caveman(agent))
                dest = Path(home) / f".{agent}/skills/caveman"
                self.assertEqual((dest / "SKILL.md").read_text(), "downloaded skill")
                self.assertEqual((dest / "references/detail.md").read_text(), "downloaded reference")
                self.assertEqual((dest / "LICENSE").read_text(), "upstream license")
                self.assertFalse((dest / "other").exists())
                self.assertEqual(fetch.call_args.args[0].full_url,
                                 "https://codeload.github.com/JuliusBrussee/caveman/zip/refs/heads/main")
                (dest / "SKILL.md").write_text("user skill")
                self.assertTrue(install.setup_caveman(agent))
                self.assertEqual(fetch.call_count, 1)
                self.assertEqual((dest / "SKILL.md").read_text(), "user skill")
                self.assertTrue(install.setup_caveman(agent, force=True))
                self.assertEqual((dest / "SKILL.md").read_text(), "downloaded skill")

    def test_failed_caveman_download_preserves_existing_skill(self):
        cases = [OSError("offline"), archive_bytes({"skills/caveman/README.md": "missing skill"}),
                 archive_bytes({"skills/caveman/../../escape": "bad path", "skills/caveman/SKILL.md": "skill"})]
        for result in cases:
            with self.subTest(result=type(result)), tempfile.TemporaryDirectory() as home:
                dest = Path(home) / "skills/caveman"
                dest.mkdir(parents=True)
                (dest / "SKILL.md").write_text("user skill")
                effect = result if isinstance(result, Exception) else lambda *a, **k: io.BytesIO(result)
                with mock.patch.object(install, "resolve_paths", return_value={"skill_home": f"{home}/skills"}), \
                     mock.patch.object(install.urllib_request, "urlopen", side_effect=effect):
                    self.assertFalse(install.setup_caveman("kiro", force=True))
                self.assertEqual((dest / "SKILL.md").read_text(), "user skill")
                self.assertFalse((Path(home) / "escape").exists())

    def test_graphify_registers_official_package_without_agent_cli(self):
        for agent in ("kiro", "copilot"):
            with self.subTest(agent=agent), tempfile.TemporaryDirectory() as home, \
                 mock.patch.object(install.os.path, "expanduser", return_value=home), \
                 mock.patch.object(install.shutil, "which", return_value=None), \
                 mock.patch.object(install, "_cli_version_string", return_value="0.9.52"), \
                 mock.patch.object(install, "_pypi_latest_version", return_value="0.9.52"), \
                 mock.patch.object(install.subprocess, "run", return_value=mock.Mock(returncode=0)) as run:
                self.assertTrue(install.setup_graphify(agent))
                run.assert_called_once_with(["graphify", "install", "--platform", agent])

    def test_graphify_bootstraps_runtime_without_agent_cli(self):
        for agent in ("kiro", "copilot"):
            with self.subTest(agent=agent), tempfile.TemporaryDirectory() as home, \
                 mock.patch.object(install.os.path, "expanduser", return_value=home), \
                 mock.patch.object(install.shutil, "which", side_effect=lambda name: "/mock/uv" if name == "uv" else None), \
                 mock.patch.object(install, "_cli_version_string", side_effect=[None, "0.9.52"]), \
                 mock.patch.object(install, "_pypi_latest_version", return_value="0.9.52"), \
                 mock.patch.object(install, "_run_text", return_value=(0, "", "")) as package, \
                 mock.patch.object(install.subprocess, "run", return_value=mock.Mock(returncode=0)) as run:
                self.assertTrue(install.setup_graphify(agent))
                package.assert_called_once_with(["uv", "tool", "install", "graphifyy"], timeout=300)
                run.assert_called_once_with(["graphify", "install", "--platform", agent])


if __name__ == "__main__":
    unittest.main()
