"""外部スキルは配布元から取得し、エージェント CLI が無くても配置し、配布元の最新へ追従する。"""
import io
import json
import tempfile
import unittest
import zipfile
from pathlib import Path
from unittest import mock

import install


SHA_A = "a" * 40
SHA_B = "b" * 40


def archive_bytes(files, name="caveman"):
    data = io.BytesIO()
    with zipfile.ZipFile(data, "w") as archive:
        for path, body in files.items():
            archive.writestr(f"{name}-main/{path}", body)
    return data.getvalue()


class FakeGitHub:
    """api.github.com はブランチ先頭のコミット、codeload はアーカイブを返す。"""

    def __init__(self, archive, head=SHA_A):
        self.archive, self.head, self.downloads = archive, head, []

    def __call__(self, request, *args, **kwargs):
        url = request.full_url
        if url.startswith("https://api.github.com/"):
            if isinstance(self.head, Exception):
                raise self.head
            return io.BytesIO(self.head.encode())
        if isinstance(self.archive, Exception):
            raise self.archive
        self.downloads.append(url)
        return io.BytesIO(self.archive)


def paths_in(home, agent="kiro"):
    base = Path(home) / f".{agent}"
    base.mkdir(parents=True, exist_ok=True)
    registry = base / "skill-registry.json"
    registry.write_text(json.dumps({"repositories": [], "installed_skills": []}))
    return {"skill_home": str(base / "skills"), "registry_path": str(registry),
            "agent_home": str(base), "user_home": home}


def skill(name="caveman"):
    return next(s for s in install.EXTERNAL_SKILLS if s["name"] == name)


class ExternalSkillInstallTest(unittest.TestCase):
    def test_downloads_from_official_source_without_cli_or_npx_and_follows_latest(self):
        data = archive_bytes({"skills/caveman/SKILL.md": "downloaded skill",
                              "skills/caveman/references/detail.md": "downloaded reference",
                              "LICENSE": "upstream license", "other/file": "do not install"})
        for agent in ("kiro", "copilot", "codex"):
            with self.subTest(agent=agent), tempfile.TemporaryDirectory() as home:
                paths = paths_in(home, agent)
                github = FakeGitHub(data)
                with mock.patch.object(install.shutil, "which", side_effect=AssertionError("エージェント CLI 不要")), \
                     mock.patch.object(install.subprocess, "run", side_effect=AssertionError("npx / git 不要")), \
                     mock.patch.object(install.urllib_request, "urlopen", side_effect=github):
                    self.assertTrue(install.install_external_skill(skill(), paths))
                    dest = Path(paths["skill_home"]) / "caveman"
                    self.assertEqual((dest / "SKILL.md").read_text(), "downloaded skill")
                    self.assertEqual((dest / "references/detail.md").read_text(), "downloaded reference")
                    self.assertEqual((dest / "LICENSE").read_text(), "upstream license")
                    self.assertFalse((dest / "other").exists())
                    self.assertEqual(github.downloads,
                                     [f"https://codeload.github.com/JuliusBrussee/caveman/zip/{SHA_A}"])
                    record = json.loads(Path(paths["registry_path"]).read_text())["installed_skills"][0]
                    self.assertEqual((record["name"], record["source_repo"], record["commit_hash"]),
                                     ("caveman", "caveman", SHA_A[:7]))

                    # 配布元が同じなら取り直さない
                    (dest / "SKILL.md").write_text("user skill")
                    self.assertTrue(install.install_external_skill(skill(), paths))
                    self.assertEqual(len(github.downloads), 1)
                    self.assertEqual((dest / "SKILL.md").read_text(), "user skill")

                    # 配布元が進んだら最新へ更新する
                    github.head = SHA_B
                    self.assertTrue(install.install_external_skill(skill(), paths))
                    self.assertEqual(github.downloads[-1],
                                     f"https://codeload.github.com/JuliusBrussee/caveman/zip/{SHA_B}")
                    self.assertEqual((dest / "SKILL.md").read_text(), "downloaded skill")
                    record = json.loads(Path(paths["registry_path"]).read_text())["installed_skills"]
                    self.assertEqual([r["commit_hash"] for r in record], [SHA_B[:7]])

    def test_keeps_installed_skill_when_pinned_or_head_unknown(self):
        data = archive_bytes({"skills/caveman/SKILL.md": "downloaded skill"})
        for case in ("pinned", "offline"):
            with self.subTest(case=case), tempfile.TemporaryDirectory() as home:
                paths = paths_in(home)
                dest = Path(paths["skill_home"]) / "caveman"
                dest.mkdir(parents=True)
                (dest / "SKILL.md").write_text("user skill")
                record = {"name": "caveman", "commit_hash": "1234567",
                          "pinned_commit": "1234567" if case == "pinned" else None}
                Path(paths["registry_path"]).write_text(json.dumps({"installed_skills": [record]}))
                github = FakeGitHub(data, head=SHA_B if case == "pinned" else OSError("offline"))
                with mock.patch.object(install.urllib_request, "urlopen", side_effect=github):
                    self.assertTrue(install.install_external_skill(skill(), paths))
                    self.assertEqual(github.downloads, [])
                    self.assertEqual((dest / "SKILL.md").read_text(), "user skill")
                    self.assertTrue(install.install_external_skill(skill(), paths, force=True))
                    self.assertEqual((dest / "SKILL.md").read_text(), "downloaded skill")

    def test_failed_download_preserves_existing_skill(self):
        cases = [OSError("offline"), archive_bytes({"skills/caveman/README.md": "missing skill"}),
                 archive_bytes({"skills/caveman/../../escape": "bad path", "skills/caveman/SKILL.md": "skill"})]
        for result in cases:
            with self.subTest(result=type(result)), tempfile.TemporaryDirectory() as home:
                paths = paths_in(home)
                dest = Path(paths["skill_home"]) / "caveman"
                dest.mkdir(parents=True)
                (dest / "SKILL.md").write_text("user skill")
                with mock.patch.object(install.urllib_request, "urlopen", side_effect=FakeGitHub(result)):
                    self.assertFalse(install.install_external_skill(skill(), paths, force=True))
                self.assertEqual((dest / "SKILL.md").read_text(), "user skill")
                self.assertFalse((Path(home) / "escape").exists())

    def test_yomiyasu_and_diagram_design_come_from_their_upstreams(self):
        self.assertEqual({s["name"]: s["repo"] for s in install.EXTERNAL_SKILLS},
                         {"caveman": "JuliusBrussee/caveman", "yomiyasu": "nanaism/yomiyasu",
                          "diagram-design": "cathrynlavery/diagram-design"})
        for name in ("yomiyasu", "diagram-design"):
            with self.subTest(name=name), tempfile.TemporaryDirectory() as home:
                paths = paths_in(home)
                data = archive_bytes({f"skills/{name}/SKILL.md": name, "LICENSE": "MIT"}, name=name)
                github = FakeGitHub(data)
                with mock.patch.object(install.urllib_request, "urlopen", side_effect=github):
                    self.assertTrue(install.install_external_skill(skill(name), paths))
                self.assertEqual((Path(paths["skill_home"]) / name / "SKILL.md").read_text(), name)
                self.assertIn(f"/{skill(name)['repo']}/zip/", github.downloads[0])

    def test_claude_takes_caveman_from_official_installer_and_others_as_skills(self):
        self.assertEqual([s["name"] for s in install.external_skills_for("claude")],
                         ["yomiyasu", "diagram-design"])
        self.assertEqual([s["name"] for s in install.external_skills_for("kiro")],
                         ["caveman", "yomiyasu", "diagram-design"])

    def test_claude_updates_installed_caveman_plugin(self):
        with tempfile.TemporaryDirectory() as home:
            hooks = Path(home) / ".claude/hooks"
            hooks.mkdir(parents=True)
            (hooks / "caveman-activate.js").write_text("")
            with mock.patch.object(install, "resolve_paths", return_value={
                     "agent_home": f"{home}/.claude", "user_home": home}), \
                 mock.patch.object(install.shutil, "which", return_value="/usr/bin/claude"), \
                 mock.patch.object(install.subprocess, "run", return_value=mock.Mock(returncode=0)) as run:
                self.assertTrue(install.setup_caveman_claude())
            self.assertEqual([c.args[0] for c in run.call_args_list],
                             [["claude", "plugin", "marketplace", "update", "caveman"],
                              ["claude", "plugin", "update", "caveman@caveman"]])

    def test_registry_registers_external_sources_for_git_skill_manager(self):
        with tempfile.TemporaryDirectory() as home:
            paths = paths_in(home)
            Path(paths["registry_path"]).unlink()
            paths.update(user_home=home, install_dir=home)
            repos = [install.external_skill_repository(s) for s in install.external_skills_for("kiro")]
            with mock.patch.object(install, "detect_repo_url", return_value=None):
                install.setup_registry([], paths, "kiro", repos)
                install.setup_registry([], paths, "kiro", repos)
            reg = json.loads(Path(paths["registry_path"]).read_text())
            self.assertEqual([(r["name"], r["url"], r["skills"], r["readonly"]) for r in reg["repositories"]],
                             [("caveman", "https://github.com/JuliusBrussee/caveman.git", ["caveman"], True),
                              ("yomiyasu", "https://github.com/nanaism/yomiyasu.git", ["yomiyasu"], True),
                              ("diagram-design", "https://github.com/cathrynlavery/diagram-design.git",
                               ["diagram-design"], True)])
            self.assertTrue(all(r["skill_root"] == "skills" for r in reg["repositories"]))

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
