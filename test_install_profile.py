"""--profile: レジストリの profiles にある名前だけをインストール対象にする。

`python -m unittest test_install_profile` で実行する。
"""
import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import install


def registry(home, profiles):
    path = Path(home, "skill-registry.json")
    path.write_text(json.dumps({"profiles": profiles}), encoding="utf-8")
    return str(path)


class ProfileSkillsTest(unittest.TestCase):
    def test_lists_the_profile_names_and_star_means_every_installable_skill(self):
        with tempfile.TemporaryDirectory() as home:
            path = registry(home, {"min": ["a", "b", "a"], "all": ["*"]})
            self.assertEqual(install._profile_skills(path, "min"), ["a", "b"])
            with mock.patch.object(install, "_discover_installable_skills",
                                   return_value=(["x", "y"], ["old"])):
                self.assertEqual(install._profile_skills(path, "all"), ["x", "y"])
            # レジストリがまだ無ければ既定の default: ["*"] だけがある
            with mock.patch.object(install, "_discover_installable_skills", return_value=(["x"], [])):
                self.assertEqual(install._profile_skills(str(Path(home, "none.json")), "default"), ["x"])

    def test_unknown_profile_stops_with_the_available_names(self):
        with tempfile.TemporaryDirectory() as home:
            path = registry(home, {"default": ["*"], "min": ["a"]})
            with self.assertRaisesRegex(ValueError, r"'typo'.*default, min"):
                install._profile_skills(path, "typo")


class PruneSkillsTest(unittest.TestCase):
    def test_removes_only_this_repos_skills_outside_the_profile(self):
        with tempfile.TemporaryDirectory() as repo, tempfile.TemporaryDirectory() as home:
            for name in ("keep", "drop"):
                Path(repo, name).mkdir()
                Path(repo, name, "SKILL.md").write_text("---\nname: x\n---\n")
            for name in ("keep", "drop", "caveman"):  # caveman は外部スキル（リポジトリに無い）
                Path(home, name).mkdir()
            with mock.patch.object(install, "REPO_SKILLS_DIR", repo):
                self.assertEqual(install.prune_skills({"skill_home": home}, ["keep"]), ["drop"])
            self.assertEqual(sorted(p.name for p in Path(home).iterdir()), ["caveman", "keep"])

    def test_registry_forgets_removed_skills(self):
        with tempfile.TemporaryDirectory() as home:
            path = Path(home, "skill-registry.json")
            path.write_text(json.dumps({"repositories": [{"name": "origin"}], "installed_skills": [
                {"name": "keep"}, {"name": "drop"}, {"name": "caveman"}]}), encoding="utf-8")
            paths = {"registry_path": str(path), "user_home": home, "install_dir": home,
                     "skill_home": home}
            with mock.patch("builtins.print"):
                install.setup_registry([{"name": "keep"}], paths, "claude", removed_skills=["drop"])
            names = [s["name"] for s in json.loads(path.read_text())["installed_skills"]]
            self.assertEqual(names, ["keep", "caveman"])


if __name__ == "__main__":
    unittest.main()
