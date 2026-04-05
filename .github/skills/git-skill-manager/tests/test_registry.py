"""registry.py のユニットテスト。"""
import json
import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "scripts"))
import registry as reg_mod


# ---------------------------------------------------------------------------
# _version_tuple
# ---------------------------------------------------------------------------

class TestVersionTuple:
    def test_normal(self):
        assert reg_mod._version_tuple("1.2.3") == (1, 2, 3)

    def test_short_two(self):
        assert reg_mod._version_tuple("1.2") == (1, 2, 0)

    def test_short_one(self):
        assert reg_mod._version_tuple("1") == (1, 0, 0)

    def test_none(self):
        assert reg_mod._version_tuple(None) == (0, 0, 0)

    def test_empty(self):
        assert reg_mod._version_tuple("") == (0, 0, 0)

    def test_prerelease_ignored(self):
        # "3-beta" は isdigit()=False のため打ち切り → (1, 2, 0)
        assert reg_mod._version_tuple("1.2.3-beta") == (1, 2, 0)

    def test_ordering(self):
        assert reg_mod._version_tuple("1.0.0") < reg_mod._version_tuple("1.0.1")
        assert reg_mod._version_tuple("1.9.0") < reg_mod._version_tuple("2.0.0")


# ---------------------------------------------------------------------------
# _read_frontmatter_version
# ---------------------------------------------------------------------------

class TestReadFrontmatterVersion:
    def _make_skill(self, tmp_path, content):
        skill_dir = tmp_path / "my-skill"
        skill_dir.mkdir()
        (skill_dir / "SKILL.md").write_text(content, encoding="utf-8")
        return str(skill_dir)

    def test_reads_version(self, tmp_path):
        content = "---\nname: my-skill\nmetadata:\n  version: 1.2.3\n---\n# Skill\n"
        path = self._make_skill(tmp_path, content)
        assert reg_mod._read_frontmatter_version(path) == "1.2.3"

    def test_reads_quoted_version(self, tmp_path):
        content = '---\nname: my-skill\nmetadata:\n  version: "2.0.0"\n---\n'
        path = self._make_skill(tmp_path, content)
        assert reg_mod._read_frontmatter_version(path) == "2.0.0"

    def test_no_frontmatter(self, tmp_path):
        path = self._make_skill(tmp_path, "# Just a skill\n")
        assert reg_mod._read_frontmatter_version(path) is None

    def test_no_version_field(self, tmp_path):
        content = "---\nname: my-skill\nmetadata:\n  tier: core\n---\n"
        path = self._make_skill(tmp_path, content)
        assert reg_mod._read_frontmatter_version(path) is None

    def test_no_skill_md(self, tmp_path):
        empty_dir = tmp_path / "empty-skill"
        empty_dir.mkdir()
        assert reg_mod._read_frontmatter_version(str(empty_dir)) is None


# ---------------------------------------------------------------------------
# _update_frontmatter_version
# ---------------------------------------------------------------------------

class TestUpdateFrontmatterVersion:
    def _make_skill(self, tmp_path, content):
        skill_dir = tmp_path / "my-skill"
        skill_dir.mkdir()
        (skill_dir / "SKILL.md").write_text(content, encoding="utf-8")
        return str(skill_dir)

    def test_updates_version(self, tmp_path):
        content = "---\nname: my-skill\nmetadata:\n  version: 1.0.0\n---\n# Body\n"
        path = self._make_skill(tmp_path, content)
        result = reg_mod._update_frontmatter_version(path, "1.0.1")
        assert result is True
        assert reg_mod._read_frontmatter_version(path) == "1.0.1"

    def test_preserves_body(self, tmp_path):
        content = "---\nname: my-skill\nmetadata:\n  version: 1.0.0\n---\n# Body\nsome text\n"
        path = self._make_skill(tmp_path, content)
        reg_mod._update_frontmatter_version(path, "2.0.0")
        text = (tmp_path / "my-skill" / "SKILL.md").read_text(encoding="utf-8")
        assert "# Body\nsome text\n" in text

    def test_no_version_returns_false(self, tmp_path):
        content = "---\nname: my-skill\nmetadata:\n  tier: core\n---\n"
        path = self._make_skill(tmp_path, content)
        result = reg_mod._update_frontmatter_version(path, "1.0.0")
        assert result is False

    def test_no_file_returns_false(self, tmp_path):
        empty = tmp_path / "no-skill"
        empty.mkdir()
        assert reg_mod._update_frontmatter_version(str(empty), "1.0.0") is False


# ---------------------------------------------------------------------------
# migrate_registry
# ---------------------------------------------------------------------------

class TestMigrateRegistry:
    def test_v1_migrates_to_v7(self):
        old = {"version": 1, "repositories": [], "installed_skills": []}
        result = reg_mod.migrate_registry(old)
        assert result["version"] == 7
        assert "auto_update" in result
        assert "promotion_policy" in result

    def test_adds_skill_fields(self):
        old = {
            "version": 1,
            "repositories": [],
            "installed_skills": [{"name": "my-skill"}],
        }
        result = reg_mod.migrate_registry(old)
        skill = result["installed_skills"][0]
        assert "enabled" in skill
        assert "feedback_history" in skill
        assert "metrics" in skill

    def test_removes_usage_stats(self):
        old = {
            "version": 6,
            "installed_skills": [
                {"name": "my-skill", "usage_stats": {"count": 5}}
            ],
        }
        result = reg_mod.migrate_registry(old)
        assert "usage_stats" not in result["installed_skills"][0]

    def test_already_v7_unchanged(self):
        reg = reg_mod.load_registry()  # 新規レジストリは v7
        assert reg["version"] == 7
        migrated = reg_mod.migrate_registry(reg)
        assert migrated["version"] == 7


# ---------------------------------------------------------------------------
# is_skill_enabled
# ---------------------------------------------------------------------------

class TestIsSkillEnabled:
    def _base_reg(self, skills=None):
        return {
            "version": 6,
            "installed_skills": skills or [],
            "profiles": {"default": ["*"]},
            "active_profile": None,
        }

    def test_unknown_skill_defaults_enabled(self):
        reg = self._base_reg()
        assert reg_mod.is_skill_enabled("unknown", reg) is True

    def test_enabled_flag_true(self):
        reg = self._base_reg([{"name": "my-skill", "enabled": True}])
        assert reg_mod.is_skill_enabled("my-skill", reg) is True

    def test_enabled_flag_false(self):
        reg = self._base_reg([{"name": "my-skill", "enabled": False}])
        assert reg_mod.is_skill_enabled("my-skill", reg) is False

    def test_profile_includes_skill(self):
        reg = self._base_reg([{"name": "skill-a", "enabled": True}])
        reg["active_profile"] = "work"
        reg["profiles"]["work"] = ["skill-a", "skill-b"]
        assert reg_mod.is_skill_enabled("skill-a", reg) is True

    def test_profile_excludes_skill(self):
        reg = self._base_reg([{"name": "skill-a", "enabled": True}])
        reg["active_profile"] = "work"
        reg["profiles"]["work"] = ["skill-b"]
        assert reg_mod.is_skill_enabled("skill-a", reg) is False

    def test_profile_wildcard_allows_all(self):
        reg = self._base_reg([{"name": "skill-a", "enabled": True}])
        reg["active_profile"] = "default"
        assert reg_mod.is_skill_enabled("skill-a", reg) is True


# ---------------------------------------------------------------------------
# load_registry / save_registry（ファイルシステム）
# ---------------------------------------------------------------------------

class TestLoadSaveRegistry:
    def test_save_and_load(self, tmp_path, monkeypatch):
        monkeypatch.setenv("USERPROFILE", str(tmp_path))
        reg = reg_mod.load_registry()
        reg["repositories"].append({"url": "https://example.com", "name": "test"})
        reg_mod.save_registry(reg)
        loaded = reg_mod.load_registry()
        assert loaded["repositories"][0]["name"] == "test"

    def test_load_nonexistent_returns_default(self, tmp_path, monkeypatch):
        monkeypatch.setenv("USERPROFILE", str(tmp_path))
        reg = reg_mod.load_registry()
        assert reg["version"] == 7
        assert reg["installed_skills"] == []
