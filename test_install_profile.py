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


if __name__ == "__main__":
    unittest.main()
